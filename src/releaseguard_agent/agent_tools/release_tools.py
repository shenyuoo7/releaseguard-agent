from dataclasses import dataclass, field, replace
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from releaseguard_agent.agents.release_risk_analysis_agent import (
    ReportDetailLevel,
    ReleaseRiskAnalysisAgent,
    ReleaseRiskAnalysisContext,
)
from releaseguard_agent.llm import LLMRuntime, OpenAIClientRequestError
from releaseguard_agent.models.retrieval_evidence import RelationPath, RetrievalEvidence
from releaseguard_agent.models.project_memory import MemoryContext, MemoryQueryBudget
from releaseguard_agent.models.relation_index import RelationQueryBudget, RelationSnapshot
from releaseguard_agent.observability import ArtifactContextTrace, ExecutionTracer
from releaseguard_agent.rag import (
    RetrievalResult,
    RuleRetrievalService,
    get_default_rule_index_path,
)
from releaseguard_agent.services.release_review_service import (
    ReleaseReviewResult,
    ReleaseReviewService,
    build_agent_advice_result,
)
from releaseguard_agent.rag.project_memory import (
    ProjectMemoryIntegrityError,
    ProjectMemoryVersionMissingError,
    ProjectMemoryStore,
    select_memory_context,
)
from releaseguard_agent.rag.relation_index import (
    RelationIndexIntegrityError,
    RelationIndexStore,
    RelationIndexVersionMissingError,
)

if TYPE_CHECKING:
    from releaseguard_agent.runtime import ToolExecutionContext, ToolRegistry
    from releaseguard_agent.runtime.tools import ToolPreparationResult


class ScanProjectTool:
    """Agent-callable wrapper around the shared deterministic review service."""

    def __init__(self, service: ReleaseReviewService | None = None) -> None:
        self._service = service or ReleaseReviewService()

    def invoke(
        self,
        project_path: Path,
        *,
        include_pytest_execution: bool,
        tracer: ExecutionTracer | None = None,
    ) -> ReleaseReviewResult:
        if tracer is None:
            return self._service.review(
                project_path=project_path,
                include_pytest_execution=include_pytest_execution,
            )
        with tracer.span("tool", tool="scan_project") as span:
            result = self._service.review(
                project_path=project_path,
                include_pytest_execution=include_pytest_execution,
            )
            span.update(
                release_allowed=result.release_allowed,
                finding_count=len(result.check_results),
            )
            return result


@dataclass(frozen=True)
class ArtifactContextRequest:
    """Explicit immutable artifact selection bound into a durable tool call."""

    project_id: str | None
    query: str
    active_run_references: tuple[str, ...]
    retrieval_mode: str
    relation_index_version: str | None
    memory_version: str | None
    relation_budget: RelationQueryBudget
    memory_budget: MemoryQueryBudget
    memory_as_of_utc: str
    recorded_trace: ArtifactContextTrace | None = None


@dataclass(frozen=True)
class ResolvedArtifactContext:
    """Safe trace identity plus process-local bounded memory content."""

    trace: ArtifactContextTrace
    memory_context: MemoryContext | None = field(repr=False)
    relation_snapshot: RelationSnapshot | None = field(repr=False)


class ArtifactContextIntegrityError(ValueError):
    """Raised when a requested immutable artifact cannot be trusted."""

    def __init__(self, trace: ArtifactContextTrace) -> None:
        super().__init__("artifact integrity verification failed")
        self.trace = trace


class EvidenceSearchTool:
    """Agent-callable wrapper around the reachable rule retrieval service."""

    def __init__(
        self,
        service: RuleRetrievalService,
        *,
        relation_snapshot_root: Path | None = None,
        memory_root: Path | None = None,
    ) -> None:
        self._service = service
        self._relation_store = (
            RelationIndexStore(relation_snapshot_root)
            if relation_snapshot_root is not None
            else None
        )
        self._memory_store = (
            ProjectMemoryStore(memory_root) if memory_root is not None else None
        )

    def resolve_artifact_context(
        self,
        request: ArtifactContextRequest,
    ) -> ResolvedArtifactContext:
        """Verify exact source versions and select memory without building caches."""

        relation_budget = tuple(sorted({
            "max_hops": request.relation_budget.max_hops,
            "max_nodes": request.relation_budget.max_nodes,
            "max_edges": request.relation_budget.max_edges,
            "max_context_characters": request.relation_budget.max_context_characters,
        }.items()))
        memory_budget = tuple(sorted({
            "top_k": request.memory_budget.top_k,
            "max_characters": request.memory_budget.max_characters,
            "max_estimated_units": request.memory_budget.max_tokens,
        }.items()))
        trace = ArtifactContextTrace(
            relation_index_version=request.relation_index_version,
            relation_mode="text_only",
            relation_budget=relation_budget,
            memory_version=request.memory_version,
            memory_mode="not_requested",
            memory_budget=memory_budget,
        )
        normalized_mode = request.retrieval_mode.strip().lower()
        relation_snapshot: RelationSnapshot | None = None
        if normalized_mode in {"local_graph", "graph_hybrid"}:
            if (
                request.recorded_trace is not None
                and request.recorded_trace.relation_fallback_reason
                == "relation_snapshot_missing"
            ):
                trace = replace(
                    trace,
                    relation_fallback_reason="relation_snapshot_missing",
                )
            elif request.relation_budget.max_hops > 2:
                trace = replace(
                    trace,
                    relation_fallback_reason="relation_hop_budget_exceeded",
                )
            elif request.relation_index_version is None:
                trace = replace(
                    trace,
                    relation_fallback_reason="relation_snapshot_not_requested",
                )
            elif self._relation_store is None:
                trace = replace(
                    trace,
                    relation_fallback_reason="relation_snapshot_unconfigured",
                )
            else:
                try:
                    relation_snapshot = self._relation_store.load(
                        request.relation_index_version
                    )
                except RelationIndexVersionMissingError:
                    trace = replace(
                        trace,
                        relation_fallback_reason="relation_snapshot_missing",
                    )
                except RelationIndexIntegrityError as exc:
                    raise ArtifactContextIntegrityError(
                        replace(
                            trace,
                            relation_fallback_reason=(
                                "relation_snapshot_integrity_failure"
                            ),
                        )
                    ) from exc
                else:
                    trace = replace(
                        trace,
                        relation_sha256=relation_snapshot.snapshot_sha256,
                        relation_mode=normalized_mode,
                    )
        elif request.relation_index_version is not None:
            trace = replace(
                trace,
                relation_fallback_reason="relation_mode_text_only",
            )

        memory_context: MemoryContext | None = None
        if (
            request.recorded_trace is not None
            and request.recorded_trace.memory_fallback_reason
            == "memory_snapshot_missing"
        ):
            trace = replace(
                trace,
                memory_mode="evidence_gap",
                memory_fallback_reason="memory_snapshot_missing",
            )
        elif request.memory_version is None:
            trace = replace(
                trace,
                memory_fallback_reason="memory_not_requested",
            )
        elif request.project_id is None:
            trace = replace(
                trace,
                memory_mode="evidence_gap",
                memory_fallback_reason="memory_project_id_required",
            )
        elif self._memory_store is None:
            trace = replace(
                trace,
                memory_mode="evidence_gap",
                memory_fallback_reason="memory_snapshot_unconfigured",
            )
        else:
            try:
                memory_snapshot = self._memory_store.load(
                    request.project_id, request.memory_version
                )
            except ProjectMemoryVersionMissingError:
                trace = replace(
                    trace,
                    memory_mode="evidence_gap",
                    memory_fallback_reason="memory_snapshot_missing",
                )
            except ProjectMemoryIntegrityError as exc:
                raise ArtifactContextIntegrityError(
                    replace(
                        trace,
                        memory_mode="integrity_failure",
                        memory_fallback_reason="memory_snapshot_integrity_failure",
                    )
                ) from exc
            else:
                memory_context = select_memory_context(
                    memory_snapshot,
                    project_id=request.project_id,
                    query=request.query,
                    active_run_references=request.active_run_references,
                    budget=request.memory_budget,
                    as_of_utc=request.memory_as_of_utc,
                )
                selected_ids = tuple(
                    sorted(item.memory_id for item in memory_context.selected)
                )
                omitted = tuple(sorted(
                    (
                        item.memory_id,
                        item.exclusion_reason or "not_selected",
                    )
                    for item in memory_context.omitted
                ))
                fallback = None
                mode = "selected"
                if not selected_ids:
                    mode = "evidence_gap"
                    fallback = (
                        "memory_budget_exhausted"
                        if any(
                            reason.endswith("_budget")
                            for _, reason in omitted
                        )
                        else "memory_no_active_records"
                    )
                trace = replace(
                    trace,
                    memory_sha256=memory_snapshot.manifest.content_sha256,
                    memory_mode=mode,
                    memory_fallback_reason=fallback,
                    selected_memory_ids=selected_ids,
                    omitted_memory=omitted,
                    memory_budget_usage=tuple(sorted({
                        "characters": memory_context.character_count,
                        "estimated_units": memory_context.token_count,
                    }.items())),
                )
        return ResolvedArtifactContext(
            trace=trace,
            memory_context=memory_context,
            relation_snapshot=relation_snapshot,
        )

    def invoke(
        self,
        query: str,
        *,
        mode: str,
        top_k: int,
        seed_rule_ids: tuple[str, ...] | None = None,
        relation_index_version: str | None = None,
        relation_budget: RelationQueryBudget | None = None,
        relation_snapshot: RelationSnapshot | None = None,
        relation_fallback_reason: str | None = None,
        tracer: ExecutionTracer | None = None,
    ) -> RetrievalResult:
        _validate_relation_inputs(relation_index_version, relation_budget)
        if tracer is None:
            return self._service.retrieve(
                query,
                mode=mode,
                top_k=top_k,
                seed_rule_ids=seed_rule_ids,
                relation_index_version=relation_index_version,
                relation_budget=relation_budget,
                relation_snapshot=relation_snapshot,
                relation_fallback_reason=relation_fallback_reason,
            )
        with tracer.span("retrieval", tool="search_rule_evidence") as span:
            result = self._service.retrieve(
                query,
                mode=mode,
                top_k=top_k,
                seed_rule_ids=seed_rule_ids,
                relation_index_version=relation_index_version,
                relation_budget=relation_budget,
                relation_snapshot=relation_snapshot,
                relation_fallback_reason=relation_fallback_reason,
            )
            span.update(
                retrieval_method=result.mode_used,
                degraded_reason=result.degraded_reason,
                relation_index_version=relation_index_version,
                relation_path_count=sum(
                    len(item.relation_paths) for item in result.evidence
                ),
                relation_path_ids=[
                    _relation_path_id(item, path)
                    for item in result.evidence
                    for path in item.relation_paths
                ],
                retrieval_candidates=[
                    {
                        "evidence_id": item.evidence_id,
                        "rule_id": item.rule_id,
                        "chunk_id": item.chunk_id,
                        "raw_score": item.raw_score,
                        "fusion_score": item.fusion_score,
                        "rerank_score": item.rerank_score,
                    }
                    for item in result.evidence
                ],
                evidence_ids=[item.evidence_id for item in result.evidence],
            )
            return result


def _validate_relation_inputs(
    relation_index_version: str | None,
    relation_budget: RelationQueryBudget | None,
) -> None:
    if relation_index_version is not None and not re.fullmatch(
        r"ri-[0-9a-f]{64}", relation_index_version
    ):
        raise ValueError("relation_index_version must be a relation snapshot version.")
    if relation_budget is not None:
        if not isinstance(relation_budget, RelationQueryBudget):
            raise ValueError("relation_budget must be a RelationQueryBudget.")


def _relation_path_id(
    evidence: RetrievalEvidence,
    path: RelationPath,
) -> str:
    """Return an ID-only trace reference without exposing graph/source text."""
    return (
        f"{evidence.evidence_id}:{path.index_version}:{','.join(path.edge_ids)}"
    )


@dataclass(frozen=True)
class RiskToolResult:
    payload: dict[str, Any]
    llm_attempted: bool
    llm_failed: bool
    error_type: str | None = None


class RiskAnalysisTool:
    """Produce guarded risk analysis with deterministic LLM fallback."""

    def __init__(
        self,
        runtime: LLMRuntime | None = None,
        *,
        locale: str = "zh-CN",
        detail_level: ReportDetailLevel = "standard",
    ) -> None:
        self._runtime = runtime
        self._locale = locale
        self._detail_level = detail_level

    @property
    def network_policy(self) -> str:
        """Expose whether this configured instance can contact an LLM provider."""

        runtime = self._runtime
        return "network" if runtime is not None and runtime.client is not None else "offline"

    def invoke(
        self,
        review: ReleaseReviewResult,
        evidence: tuple[RetrievalEvidence, ...],
        tracer: ExecutionTracer | None = None,
    ) -> RiskToolResult:
        if tracer is not None:
            with tracer.span("tool", tool="analyze_risk") as span:
                result = self._invoke(review, evidence, tracer=tracer)
                span.update(
                    llm_attempted=result.llm_attempted,
                    llm_failed=result.llm_failed,
                    error_type=result.error_type,
                    evidence_ids=[item.evidence_id for item in evidence],
                )
                return result
        return self._invoke(review, evidence, tracer=None)

    def _invoke(
        self,
        review: ReleaseReviewResult,
        evidence: tuple[RetrievalEvidence, ...],
        *,
        tracer: ExecutionTracer | None,
    ) -> RiskToolResult:
        runtime = self._runtime
        if runtime is None or runtime.client is None:
            return RiskToolResult(
                payload=_deterministic_risk_payload(review, evidence),
                llm_attempted=False,
                llm_failed=False,
            )
        advice = review.advice_result or build_agent_advice_result(
            project_path=review.project_path,
            results=review.check_results,
        )
        context = ReleaseRiskAnalysisContext(
            advice_result=advice,
            retrieval_evidence=evidence,
            check_results=tuple(
                {
                    **item.to_dict(),
                    "check_result_id": _check_result_id(index, item.rule_id),
                }
                for index, item in enumerate(review.check_results, start=1)
            ),
            locale=self._locale,
            detail_level=self._detail_level,
        )
        try:
            if tracer is None:
                result = ReleaseRiskAnalysisAgent(
                    llm_client=runtime.client,
                    model=runtime.model,
                    temperature=0.0,
                ).analyze(context)
            else:
                with tracer.span(
                    "llm",
                    tool="llm.complete",
                    provider=runtime.provider,
                    model=runtime.model,
                ) as llm_span:
                    result = ReleaseRiskAnalysisAgent(
                        llm_client=runtime.client,
                        model=runtime.model,
                        temperature=0.0,
                    ).analyze(context)
                    llm_span.update(
                        token_usage=dict(result.llm_response.usage),
                        evidence_ids=list(result.analysis.evidence_ids),
                    )
        except Exception as exc:
            return RiskToolResult(
                payload=_deterministic_risk_payload(review, evidence),
                llm_attempted=True,
                llm_failed=True,
                error_type=_llm_error_type(exc),
            )
        payload = result.analysis.to_dict()
        payload["analysis_source"] = "llm"
        return RiskToolResult(
            payload=payload,
            llm_attempted=True,
            llm_failed=False,
        )


class FixPlanTool:
    """Build a concrete plan without modifying the reviewed repository."""

    def invoke(
        self,
        review: ReleaseReviewResult,
        risk_payload: dict[str, Any],
        tracer: ExecutionTracer | None = None,
    ) -> tuple[dict[str, Any], ...]:
        if tracer is not None:
            with tracer.span("tool", tool="build_fix_plan") as span:
                result = self._invoke(review, risk_payload)
                span.update(step_count=len(result))
                return result
        return self._invoke(review, risk_payload)

    def _invoke(
        self,
        review: ReleaseReviewResult,
        risk_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        model_plan = risk_payload.get("fix_plan")
        if isinstance(model_plan, list) and model_plan:
            return tuple(dict(step) for step in model_plan if isinstance(step, dict))
        blocking = [
            result for result in review.check_results if result.should_block_release
        ]
        return tuple(
            {
                "priority": index,
                "title": result.title,
                "action": result.recommendation or result.message,
                "rule_ids": [result.rule_id] if result.rule_id else [],
                "validation": (
                    "Apply the change manually, then run ReleaseGuard verification."
                ),
            }
            for index, result in enumerate(blocking, start=1)
        )


@dataclass(frozen=True)
class ReleaseWorkflowTools:
    scan: ScanProjectTool
    evidence: EvidenceSearchTool
    risk: RiskAnalysisTool
    fix_plan: FixPlanTool


def build_release_tool_registry(
    tools: ReleaseWorkflowTools | None = None,
    *,
    allowed_roots: tuple[Path, ...] | None = None,
) -> "ToolRegistry":
    """Register durable, read-only adapters without changing legacy tool APIs."""

    from releaseguard_agent.runtime import ToolRegistry, ToolSpec

    workflow_tools = tools or ReleaseWorkflowTools(
        scan=ScanProjectTool(),
        evidence=EvidenceSearchTool(RuleRetrievalService(get_default_rule_index_path())),
        risk=RiskAnalysisTool(),
        fix_plan=FixPlanTool(),
    )
    review_roots = allowed_roots or (Path(__file__).resolve().parents[3],)
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="scan_project",
            version="1",
            input_schema={"project_path": str, "include_pytest_execution": bool},
            output_schema={"review_ref": str, "release_allowed": bool, "report": dict},
            side_effect="read_only",
            allowed_roots=review_roots,
            network_policy="offline",
            timeout_ms=60_000,
            max_retries=1,
            budget_cost=1,
            required_approval_scope=None,
        ),
        lambda args, context: _runtime_scan(workflow_tools.scan, args, context),
    )
    registry.register(
        ToolSpec(
            name="search_rule_evidence",
            version="1",
            input_schema={
                "review_ref": str,
                "retrieval_mode": str,
                "top_k": int,
                "minimum_evidence": int,
            },
            output_schema={
                "evidence_ref": str,
                "evidence_output": dict,
            },
            side_effect="read_only",
            allowed_roots=(),
            network_policy="offline",
            timeout_ms=10_000,
            max_retries=0,
            budget_cost=1,
            required_approval_scope=None,
        ),
        lambda args, context: _runtime_evidence(workflow_tools.evidence, args, context),
    )
    registry.register(
        ToolSpec(
            name="search_rule_evidence",
            version="2",
            input_schema={
                "review_ref": str,
                "retrieval_mode": str,
                "top_k": int,
                "minimum_evidence": int,
                "relation_index_version": (str, type(None)),
                "memory_project_id": (str, type(None)),
                "memory_version": (str, type(None)),
                "relation_budget": dict,
                "memory_budget": dict,
                "memory_as_of_utc": str,
                "artifact_context": dict,
                "artifact_context_ref": str,
            },
            output_schema={
                "evidence_ref": str,
                "evidence_output": dict,
            },
            side_effect="read_only",
            allowed_roots=(),
            network_policy="offline",
            timeout_ms=10_000,
            max_retries=0,
            budget_cost=1,
            required_approval_scope=None,
        ),
        lambda args, context: _runtime_evidence(workflow_tools.evidence, args, context),
        preparer=lambda args, context: _prepare_runtime_evidence(
            workflow_tools.evidence, args, context
        ),
    )
    registry.register(
        ToolSpec(
            name="analyze_risk",
            version="1",
            input_schema={"review_ref": str, "evidence_ref": str},
            output_schema={"risk_ref": str, "risk_output": dict},
            side_effect=(
                "network"
                if getattr(workflow_tools.risk, "network_policy", "network")
                == "network"
                else "read_only"
            ),
            allowed_roots=(),
            network_policy=getattr(workflow_tools.risk, "network_policy", "network"),
            timeout_ms=30_000,
            max_retries=0,
            budget_cost=1,
            required_approval_scope=(
                "network.llm"
                if getattr(workflow_tools.risk, "network_policy", "network")
                == "network"
                else None
            ),
        ),
        lambda args, context: _runtime_risk(workflow_tools.risk, args, context),
    )
    registry.register(
        ToolSpec(
            name="build_fix_plan",
            version="1",
            input_schema={
                "review_ref": str,
                "evidence_ref": str,
                "risk_ref": str,
            },
            output_schema={"fix_plan_output": dict},
            side_effect="read_only",
            allowed_roots=(),
            network_policy="offline",
            timeout_ms=10_000,
            max_retries=0,
            budget_cost=1,
            required_approval_scope=None,
        ),
        lambda args, context: _runtime_fix_plan(workflow_tools.fix_plan, args, context),
    )
    return registry


def _runtime_scan(
    tool: ScanProjectTool,
    args: dict[str, Any],
    context: "ToolExecutionContext",
) -> dict[str, Any]:
    review = tool.invoke(
        Path(args["project_path"]),
        include_pytest_execution=args["include_pytest_execution"],
    )
    report = review.to_dict()
    review_ref = f"review:{_runtime_digest(report)}"
    context.references[review_ref] = review
    return {
        "review_ref": review_ref,
        "release_allowed": review.release_allowed,
        "report": report,
    }


def _runtime_evidence(
    tool: EvidenceSearchTool,
    args: dict[str, Any],
    context: "ToolExecutionContext",
) -> dict[str, Any]:
    from releaseguard_agent.agents.role_agents import (
        EvidenceAgent,
        EvidenceAgentInput,
        EvidenceAgentOutput,
    )

    review = context.references.get(args["review_ref"])
    if not isinstance(review, ReleaseReviewResult):
        raise ValueError("unknown review reference")
    relation_budget = _relation_budget_from_args(args)
    memory_budget = _memory_budget_from_args(args)
    raw_artifact_context = args.get("artifact_context")
    artifact_context = (
        ArtifactContextTrace.from_dict(raw_artifact_context)
        if isinstance(raw_artifact_context, dict)
        else None
    )
    raw_context_ref = args.get("artifact_context_ref")
    resolved_context = (
        context.references.get(raw_context_ref)
        if isinstance(raw_context_ref, str)
        else None
    )
    if resolved_context is not None and not isinstance(
        resolved_context, ResolvedArtifactContext
    ):
        raise ValueError("artifact context reference is invalid")
    memory_context = (
        resolved_context.memory_context if resolved_context is not None else None
    )
    relation_snapshot = (
        resolved_context.relation_snapshot if resolved_context is not None else None
    )
    if (
        artifact_context is not None
        and artifact_context.selected_memory_ids
        and memory_context is None
    ):
        raise ValueError("selected memory context is unavailable")
    output = EvidenceAgent(tool).run(
        EvidenceAgentInput(
            review=review,
            retrieval_mode=args["retrieval_mode"],
            top_k=args["top_k"],
            minimum_evidence=args["minimum_evidence"],
            relation_index_version=args.get("relation_index_version"),
            relation_budget=relation_budget,
            relation_snapshot=relation_snapshot,
            memory_context=memory_context,
            memory_budget=memory_budget,
            artifact_context=artifact_context,
        )
    )
    payload = output.to_durable_dict()
    evidence_ref = f"evidence:{_runtime_digest(payload)}"
    # Downstream roles consume the same ID/provenance-only contract on a fresh
    # run and after recovery; raw retrieval text never becomes durable state.
    context.references[evidence_ref] = EvidenceAgentOutput.from_dict(
        payload
    ).evidence
    return {"evidence_ref": evidence_ref, "evidence_output": payload}


def _prepare_runtime_evidence(
    tool: EvidenceSearchTool,
    args: dict[str, Any],
    context: "ToolExecutionContext",
) -> "ToolPreparationResult":
    from releaseguard_agent.agents.role_agents import evidence_query
    from releaseguard_agent.runtime.tools import ToolPreparationResult

    review = context.references.get(args["review_ref"])
    if not isinstance(review, ReleaseReviewResult):
        raise ValueError("unknown review reference")
    relation_budget = _relation_budget_from_args(args)
    memory_budget = _memory_budget_from_args(args)
    if relation_budget is None or memory_budget is None:
        raise ValueError("artifact budgets are required")
    query, _, relevant_rule_ids = evidence_query(review)
    raw_recorded = args.get("artifact_context")
    recorded_trace = (
        ArtifactContextTrace.from_dict(raw_recorded)
        if isinstance(raw_recorded, dict)
        else None
    )
    try:
        resolved = tool.resolve_artifact_context(
            ArtifactContextRequest(
                project_id=args.get("memory_project_id"),
                query=query,
                active_run_references=tuple(sorted({
                    args["review_ref"], *relevant_rule_ids
                })),
                retrieval_mode=args["retrieval_mode"],
                relation_index_version=args.get("relation_index_version"),
                memory_version=args.get("memory_version"),
                relation_budget=relation_budget,
                memory_budget=memory_budget,
                memory_as_of_utc=args["memory_as_of_utc"],
                recorded_trace=recorded_trace,
            )
        )
    except ArtifactContextIntegrityError as exc:
        return ToolPreparationResult(
            arguments=_prepared_evidence_arguments(
                args,
                exc.trace,
                artifact_context_ref=(
                    f"artifact-context:{_runtime_digest(exc.trace.to_dict())}"
                ),
            ),
            error_type="artifact_integrity_failure",
        )
    context_ref = f"artifact-context:{_runtime_digest(resolved.trace.to_dict())}"
    prepared_args = _prepared_evidence_arguments(
        args, resolved.trace, artifact_context_ref=context_ref
    )
    recorded_ref = args.get("artifact_context_ref")
    if raw_recorded is not None and (
        raw_recorded != resolved.trace.to_dict() or recorded_ref != context_ref
    ):
        return ToolPreparationResult(
            arguments=dict(args),
            error_type="artifact_context_mismatch",
        )
    references = (
        {context_ref: resolved}
        if resolved.memory_context is not None or resolved.relation_snapshot is not None
        else {}
    )
    return ToolPreparationResult(arguments=prepared_args, references=references)


def _prepared_evidence_arguments(
    args: dict[str, Any],
    trace: ArtifactContextTrace,
    *,
    artifact_context_ref: str,
) -> dict[str, Any]:
    return {
        "review_ref": args["review_ref"],
        "retrieval_mode": args["retrieval_mode"],
        "top_k": args["top_k"],
        "minimum_evidence": args["minimum_evidence"],
        "relation_index_version": args.get("relation_index_version"),
        "memory_project_id": args.get("memory_project_id"),
        "memory_version": args.get("memory_version"),
        "relation_budget": _required_relation_budget(args),
        "memory_budget": _required_memory_budget(args),
        "memory_as_of_utc": args["memory_as_of_utc"],
        "artifact_context": trace.to_dict(),
        "artifact_context_ref": artifact_context_ref,
    }


def _relation_budget_from_args(args: dict[str, Any]) -> RelationQueryBudget | None:
    raw = args.get("relation_budget")
    if raw is None:
        return None
    if isinstance(raw, RelationQueryBudget):
        return raw
    if not isinstance(raw, dict):
        raise ValueError("relation_budget is invalid")
    return RelationQueryBudget(**raw)


def _memory_budget_from_args(args: dict[str, Any]) -> MemoryQueryBudget | None:
    raw = args.get("memory_budget")
    if raw is None:
        return None
    if isinstance(raw, MemoryQueryBudget):
        return raw
    if not isinstance(raw, dict):
        raise ValueError("memory_budget is invalid")
    try:
        return MemoryQueryBudget(
            top_k=raw["top_k"],
            max_characters=raw["max_characters"],
            max_tokens=raw["max_context_units"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("memory_budget is invalid") from exc


def _required_relation_budget(args: dict[str, Any]) -> dict[str, int]:
    budget = _relation_budget_from_args(args)
    if budget is None:
        raise ValueError("relation_budget is required")
    return {
        "max_hops": budget.max_hops,
        "max_nodes": budget.max_nodes,
        "max_edges": budget.max_edges,
        "max_context_characters": budget.max_context_characters,
    }


def _required_memory_budget(args: dict[str, Any]) -> dict[str, int]:
    budget = _memory_budget_from_args(args)
    if budget is None:
        raise ValueError("memory_budget is required")
    return {
        "top_k": budget.top_k,
        "max_characters": budget.max_characters,
        "max_context_units": budget.max_tokens,
    }


def _runtime_risk(
    tool: RiskAnalysisTool,
    args: dict[str, Any],
    context: "ToolExecutionContext",
) -> dict[str, Any]:
    from releaseguard_agent.agents.role_agents import RiskAgent, RiskAgentInput

    review = context.references.get(args["review_ref"])
    evidence = context.references.get(args["evidence_ref"])
    if not isinstance(review, ReleaseReviewResult) or not isinstance(evidence, tuple):
        raise ValueError("unknown review or evidence reference")
    output = RiskAgent(tool).run(
        RiskAgentInput(review=review, evidence=evidence)
    )
    payload = output.to_dict()
    risk_ref = f"risk:{_runtime_digest(payload)}"
    context.references[risk_ref] = output
    return {"risk_ref": risk_ref, "risk_output": payload}


def _runtime_fix_plan(
    tool: FixPlanTool,
    args: dict[str, Any],
    context: "ToolExecutionContext",
) -> dict[str, Any]:
    from releaseguard_agent.agents.role_agents import (
        FixPlannerAgent,
        FixPlannerAgentInput,
        RiskAgentOutput,
    )

    review = context.references.get(args["review_ref"])
    evidence = context.references.get(args["evidence_ref"])
    risk = context.references.get(args["risk_ref"])
    if (
        not isinstance(review, ReleaseReviewResult)
        or not isinstance(evidence, tuple)
        or not isinstance(risk, RiskAgentOutput)
    ):
        raise ValueError("unknown review, evidence, or risk reference")
    output = FixPlannerAgent(tool).run(
        FixPlannerAgentInput(review=review, risk=risk, evidence=evidence)
    )
    return {"fix_plan_output": output.to_dict()}


def _runtime_digest(value: object) -> str:
    """Avoid importing durable runtime modules during legacy tool import."""

    from releaseguard_agent.runtime.models import sha256_json

    return sha256_json(value)


def _deterministic_risk_payload(
    review: ReleaseReviewResult,
    evidence: tuple[RetrievalEvidence, ...],
) -> dict[str, Any]:
    blocking = [
        result for result in review.check_results if result.should_block_release
    ]
    risk_order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    risk_level = max(
        (result.risk_level.value for result in blocking),
        key=lambda value: risk_order[value],
        default="low",
    )
    return {
        "analysis_source": "deterministic",
        "risk_level": risk_level,
        "summary": (
            f"发现 {len(blocking)} 项确定性阻断问题。"
            if blocking
            else "未发现确定性阻断问题。"
        ),
        "release_allowed": review.release_allowed,
        "release_status": "release" if review.release_allowed else "block",
        "evidence_ids": [item.evidence_id for item in evidence],
        "fix_plan": [],
    }


def _check_result_id(index: int, rule_id: str | None) -> str:
    return f"CHECK-{index:03d}-{rule_id or 'NO-RULE'}"


def _llm_error_type(exc: Exception) -> str:
    if not isinstance(exc, OpenAIClientRequestError):
        return type(exc).__name__
    if exc.status_code in {401, 403}:
        return "authentication_failed"
    if exc.status_code == 404:
        return "model_or_url_not_found"
    if exc.status_code == 429:
        return "rate_limited"
    if "timeout" in exc.error_type.lower():
        return "timeout"
    return "provider_error"
