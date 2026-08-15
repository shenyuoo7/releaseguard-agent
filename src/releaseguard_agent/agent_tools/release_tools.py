from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from releaseguard_agent.agents.release_risk_analysis_agent import (
    ReportDetailLevel,
    ReleaseRiskAnalysisAgent,
    ReleaseRiskAnalysisContext,
)
from releaseguard_agent.llm import LLMRuntime, OpenAIClientRequestError
from releaseguard_agent.models.retrieval_evidence import RetrievalEvidence
from releaseguard_agent.observability import ExecutionTracer
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

if TYPE_CHECKING:
    from releaseguard_agent.runtime import ToolExecutionContext, ToolRegistry


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


class EvidenceSearchTool:
    """Agent-callable wrapper around the reachable rule retrieval service."""

    def __init__(self, service: RuleRetrievalService) -> None:
        self._service = service

    def invoke(
        self,
        query: str,
        *,
        mode: str,
        top_k: int,
        tracer: ExecutionTracer | None = None,
    ) -> RetrievalResult:
        if tracer is None:
            return self._service.retrieve(query, mode=mode, top_k=top_k)
        with tracer.span("retrieval", tool="search_rule_evidence") as span:
            result = self._service.retrieve(query, mode=mode, top_k=top_k)
            span.update(
                retrieval_method=result.mode_used,
                degraded_reason=result.degraded_reason,
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
    )

    review = context.references.get(args["review_ref"])
    if not isinstance(review, ReleaseReviewResult):
        raise ValueError("unknown review reference")
    output = EvidenceAgent(tool).run(
        EvidenceAgentInput(
            review=review,
            retrieval_mode=args["retrieval_mode"],
            top_k=args["top_k"],
            minimum_evidence=args["minimum_evidence"],
        )
    )
    payload = output.to_dict()
    evidence_ref = f"evidence:{_runtime_digest(payload)}"
    context.references[evidence_ref] = output.evidence
    return {"evidence_ref": evidence_ref, "evidence_output": payload}


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
