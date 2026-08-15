from dataclasses import dataclass, replace
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from releaseguard_agent.agent_tools import (
    EvidenceSearchTool,
    FixPlanTool,
    RiskAnalysisTool,
)
from releaseguard_agent.models.check_result import CheckStatus
from releaseguard_agent.models.project_memory import MemoryContext, MemoryQueryBudget
from releaseguard_agent.models.relation_index import RelationQueryBudget, RelationSnapshot
from releaseguard_agent.models.retrieval_evidence import RelationPath, RetrievalEvidence
from releaseguard_agent.observability import ArtifactContextTrace, ExecutionTracer
from releaseguard_agent.services.release_review_service import ReleaseReviewResult


@dataclass(frozen=True)
class EvidenceAgentInput:
    review: ReleaseReviewResult
    retrieval_mode: str = "hybrid"
    top_k: int = 5
    minimum_evidence: int = 1
    relation_index_version: str | None = None
    relation_budget: RelationQueryBudget | None = None
    relation_snapshot: RelationSnapshot | None = None
    memory_context: MemoryContext | None = None
    memory_budget: MemoryQueryBudget | None = None
    artifact_context: ArtifactContextTrace | None = None


@dataclass(frozen=True)
class EvidenceAgentOutput:
    evidence: tuple[RetrievalEvidence, ...]
    sufficient: bool
    supplemental_attempted: bool
    manual_review_required: bool
    degraded_reason: str | None
    artifact_context: ArtifactContextTrace | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": [item.to_dict() for item in self.evidence],
            "sufficient": self.sufficient,
            "supplemental_attempted": self.supplemental_attempted,
            "manual_review_required": self.manual_review_required,
            "degraded_reason": self.degraded_reason,
            "artifact_context": (
                self.artifact_context.to_dict()
                if self.artifact_context is not None
                else None
            ),
        }

    def to_durable_dict(self) -> dict[str, Any]:
        """Serialize evidence identity/provenance without raw retrieved text."""

        value = self.to_dict()
        value["evidence"] = [
            {**item.to_dict(), "text": "", "metadata": {}}
            for item in self.evidence
        ]
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceAgentOutput":
        expected = {
                "evidence",
                "sufficient",
                "supplemental_attempted",
                "manual_review_required",
                "degraded_reason",
            }
        actual = frozenset(value)
        if actual not in {
            frozenset(expected),
            frozenset((*expected, "artifact_context")),
        }:
            raise ValueError("evidence output keys do not match")
        raw_evidence = value["evidence"]
        if not isinstance(raw_evidence, (list, tuple)) or not all(
            isinstance(item, Mapping) for item in raw_evidence
        ):
            raise ValueError("evidence output evidence must be a list")
        degraded_reason = value["degraded_reason"]
        if degraded_reason is not None and not isinstance(degraded_reason, str):
            raise ValueError("degraded_reason must be a string or null")
        raw_context = value.get("artifact_context")
        if raw_context is not None and not isinstance(raw_context, Mapping):
            raise ValueError("artifact_context must be an object or null")
        return cls(
            evidence=tuple(
                _retrieval_evidence_from_dict(item)
                for item in raw_evidence
            ),
            sufficient=_boolean(value["sufficient"], "sufficient"),
            supplemental_attempted=_boolean(
                value["supplemental_attempted"], "supplemental_attempted"
            ),
            manual_review_required=_boolean(
                value["manual_review_required"], "manual_review_required"
            ),
            degraded_reason=degraded_reason,
            artifact_context=(
                ArtifactContextTrace.from_dict(raw_context)
                if isinstance(raw_context, Mapping)
                else None
            ),
        )


class EvidenceAgent:
    """Retrieve evidence linked to actionable findings or verified strengths."""

    def __init__(
        self,
        tool: EvidenceSearchTool,
        tracer: ExecutionTracer | None = None,
    ) -> None:
        self._tool = tool
        self._tracer = tracer

    def run(self, request: EvidenceAgentInput) -> EvidenceAgentOutput:
        query, grounding_results, relevant_rule_ids = evidence_query(
            request.review,
            memory_context=request.memory_context,
        )
        invoke_args: dict[str, Any] = {
            "mode": request.retrieval_mode,
            "top_k": request.top_k,
            "tracer": self._tracer,
        }
        if request.retrieval_mode.strip().lower() in {
            "local_graph",
            "graph_hybrid",
        }:
            invoke_args.update(
                seed_rule_ids=tuple(sorted(relevant_rule_ids)),
                relation_index_version=request.relation_index_version,
                relation_budget=request.relation_budget,
                relation_snapshot=request.relation_snapshot,
                relation_fallback_reason=(
                    request.artifact_context.relation_fallback_reason
                    if request.artifact_context is not None
                    else None
                ),
            )
        initial = self._tool.invoke(query, **invoke_args)
        combined = {
            item.chunk_id: item
            for item in initial.evidence
            if not relevant_rule_ids or item.rule_id in relevant_rule_ids
        }
        supplemental_attempted = False
        for rule_id in sorted(relevant_rule_ids):
            if any(item.rule_id == rule_id for item in combined.values()):
                continue
            supplemental_attempted = True
            exact = self._tool.invoke(
                rule_id,
                mode="exact",
                top_k=10,
                tracer=self._tracer,
            )
            combined.update(
                {
                    item.chunk_id: item
                    for item in exact.evidence
                    if item.rule_id == rule_id
                }
            )
        sufficient = _evidence_is_sufficient(
            tuple(combined.values()), request.minimum_evidence
        )
        if not sufficient:
            supplemental_attempted = True
            for result in grounding_results:
                if not result.rule_id:
                    continue
                exact = self._tool.invoke(
                    result.rule_id,
                    mode="exact",
                    top_k=10,
                    tracer=self._tracer,
                )
                combined.update(
                    {
                        item.chunk_id: item
                        for item in exact.evidence
                        if item.rule_id == result.rule_id
                    }
                )
            sufficient = _evidence_is_sufficient(
                tuple(combined.values()), request.minimum_evidence
            )
        return EvidenceAgentOutput(
            evidence=tuple(combined.values()),
            sufficient=sufficient,
            supplemental_attempted=supplemental_attempted,
            manual_review_required=not sufficient,
            degraded_reason=initial.degraded_reason,
            artifact_context=_trace_with_retrieval(
                request.artifact_context, initial
            ),
        )


def evidence_query(
    review: ReleaseReviewResult,
    *,
    memory_context: MemoryContext | None = None,
) -> tuple[str, list[Any], set[str]]:
    """Build the local evidence query without changing deterministic findings."""

    actionable = [
        result
        for result in review.check_results
        if result.status in {CheckStatus.FAILED, CheckStatus.WARNING}
    ]
    grounding_results = actionable or [
        result for result in review.check_results if result.status == CheckStatus.PASSED
    ]
    relevant_rule_ids = {
        result.rule_id for result in grounding_results if result.rule_id
    }
    query = " ".join(
        part
        for result in grounding_results
        for part in (result.rule_id or "", result.title, result.message)
    )
    if not query.strip():
        query = "release readiness"
    if memory_context is not None and memory_context.content:
        query = f"{query} {memory_context.content}"
    return query, grounding_results, relevant_rule_ids


@dataclass(frozen=True)
class RiskAgentInput:
    review: ReleaseReviewResult
    evidence: tuple[RetrievalEvidence, ...]


@dataclass(frozen=True)
class RiskAgentOutput:
    analysis: dict[str, Any]
    evidence_ids: tuple[str, ...]
    llm_attempted: bool
    llm_failed: bool
    error_type: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis": dict(self.analysis),
            "evidence_ids": list(self.evidence_ids),
            "llm_attempted": self.llm_attempted,
            "llm_failed": self.llm_failed,
            "error_type": self.error_type,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RiskAgentOutput":
        _exact_keys(
            value,
            {
                "analysis",
                "evidence_ids",
                "llm_attempted",
                "llm_failed",
                "error_type",
            },
            "risk output",
        )
        analysis = value["analysis"]
        evidence_ids = value["evidence_ids"]
        error_type = value["error_type"]
        if not isinstance(analysis, Mapping):
            raise ValueError("risk analysis must be an object")
        if not isinstance(evidence_ids, (list, tuple)) or not all(
            isinstance(item, str) and item for item in evidence_ids
        ):
            raise ValueError("risk evidence_ids must contain strings")
        if error_type is not None and not isinstance(error_type, str):
            raise ValueError("risk error_type must be a string or null")
        return cls(
            analysis=dict(analysis),
            evidence_ids=tuple(evidence_ids),
            llm_attempted=_boolean(value["llm_attempted"], "llm_attempted"),
            llm_failed=_boolean(value["llm_failed"], "llm_failed"),
            error_type=error_type,
        )


class RiskAgent:
    """Analyze risk while preserving the deterministic release decision."""

    def __init__(
        self,
        tool: RiskAnalysisTool,
        tracer: ExecutionTracer | None = None,
    ) -> None:
        self._tool = tool
        self._tracer = tracer

    def run(self, request: RiskAgentInput) -> RiskAgentOutput:
        result = self._tool.invoke(
            request.review,
            request.evidence,
            tracer=self._tracer,
        )
        analysis = dict(result.payload)
        analysis["release_allowed"] = request.review.release_allowed
        analysis["release_status"] = (
            "release" if request.review.release_allowed else "block"
        )
        evidence_ids = tuple(
            value
            for value in analysis.get("evidence_ids", [])
            if isinstance(value, str)
        )
        return RiskAgentOutput(
            analysis=analysis,
            evidence_ids=evidence_ids,
            llm_attempted=result.llm_attempted,
            llm_failed=result.llm_failed,
            error_type=result.error_type,
        )


@dataclass(frozen=True)
class FixPlannerAgentInput:
    review: ReleaseReviewResult
    risk: RiskAgentOutput
    evidence: tuple[RetrievalEvidence, ...]


@dataclass(frozen=True)
class FixPlannerAgentOutput:
    steps: tuple[dict[str, Any], ...]
    covered_rule_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    requires_manual_changes: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": [dict(step) for step in self.steps],
            "covered_rule_ids": list(self.covered_rule_ids),
            "evidence_ids": list(self.evidence_ids),
            "requires_manual_changes": self.requires_manual_changes,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixPlannerAgentOutput":
        _exact_keys(
            value,
            {
                "steps",
                "covered_rule_ids",
                "evidence_ids",
                "requires_manual_changes",
            },
            "fix planner output",
        )
        raw_steps = value["steps"]
        covered = value["covered_rule_ids"]
        evidence_ids = value["evidence_ids"]
        if not isinstance(raw_steps, (list, tuple)) or not all(
            isinstance(step, Mapping) for step in raw_steps
        ):
            raise ValueError("fix planner steps must contain objects")
        for values, name in (
            (covered, "covered_rule_ids"),
            (evidence_ids, "evidence_ids"),
        ):
            if not isinstance(values, (list, tuple)) or not all(
                isinstance(item, str) and item for item in values
            ):
                raise ValueError(f"{name} must contain strings")
        return cls(
            steps=tuple(dict(step) for step in raw_steps),
            covered_rule_ids=tuple(covered),
            evidence_ids=tuple(evidence_ids),
            requires_manual_changes=_boolean(
                value["requires_manual_changes"], "requires_manual_changes"
            ),
        )


class FixPlannerAgent:
    """Create an actionable manual plan for every warning or blocker."""

    def __init__(
        self,
        tool: FixPlanTool,
        tracer: ExecutionTracer | None = None,
    ) -> None:
        self._tool = tool
        self._tracer = tracer

    def run(self, request: FixPlannerAgentInput) -> FixPlannerAgentOutput:
        raw_steps = list(
            self._tool.invoke(
                request.review,
                request.risk.analysis,
                tracer=self._tracer,
            )
        )
        actionable_results = [
            result
            for result in request.review.check_results
            if result.status in {CheckStatus.FAILED, CheckStatus.WARNING}
        ]
        actionable_rule_ids = {
            result.rule_id
            for result in actionable_results
            if result.rule_id
        }
        covered = {
            rule_id
            for step in raw_steps
            for rule_id in step.get("rule_ids", [])
            if isinstance(rule_id, str)
        }
        for result in actionable_results:
            if (
                not result.rule_id
                or result.rule_id in covered
            ):
                continue
            raw_steps.append(
                _fallback_action(
                    request.review.project_path,
                    result,
                    priority=len(raw_steps) + 1,
                )
            )
            covered.add(result.rule_id)
        evidence_by_rule: dict[str, list[str]] = {}
        for item in request.evidence:
            evidence_by_rule.setdefault(item.rule_id, []).append(item.evidence_id)
        check_ids_by_rule = {
            result.rule_id: f"CHECK-{index:03d}-{result.rule_id or 'NO-RULE'}"
            for index, result in enumerate(request.review.check_results, start=1)
            if result.rule_id
        }
        normalized_steps: list[dict[str, Any]] = []
        for step in raw_steps:
            normalized = _normalize_action(
                request.review.project_path,
                actionable_results,
                step,
            )
            step_rule_ids = [
                value
                for value in normalized.get("rule_ids", [])
                if isinstance(value, str)
            ]
            normalized["evidence_ids"] = sorted(
                {
                    evidence_id
                    for rule_id in step_rule_ids
                    for evidence_id in evidence_by_rule.get(rule_id, [])
                }
            )
            normalized["related_check_ids"] = list(dict.fromkeys([
                *[
                    value
                    for value in normalized.get("related_check_ids", [])
                    if isinstance(value, str)
                ],
                *[
                    check_ids_by_rule[rule_id]
                    for rule_id in step_rule_ids
                    if rule_id in check_ids_by_rule
                ],
            ]))
            normalized_steps.append(normalized)
        all_evidence_ids = tuple(sorted({
            evidence_id
            for step in normalized_steps
            for evidence_id in step.get("evidence_ids", [])
            if isinstance(evidence_id, str)
        }))
        return FixPlannerAgentOutput(
            steps=tuple(normalized_steps),
            covered_rule_ids=tuple(sorted(actionable_rule_ids.intersection(covered))),
            evidence_ids=all_evidence_ids,
        )


@dataclass(frozen=True)
class VerifierAgentInput:
    before: ReleaseReviewResult
    after: ReleaseReviewResult


@dataclass(frozen=True)
class VerifierAgentOutput:
    resolved: tuple[str, ...]
    new: tuple[str, ...]
    unchanged: tuple[str, ...]
    before_release_allowed: bool
    release_allowed: bool
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": list(self.resolved),
            "new": list(self.new),
            "unchanged": list(self.unchanged),
            "before_release_allowed": self.before_release_allowed,
            "release_allowed": self.release_allowed,
            "status": self.status,
        }


class VerifierAgent:
    """Compare two independent scans after a user-applied change."""

    def run(self, request: VerifierAgentInput) -> VerifierAgentOutput:
        before = _issue_ids(request.before)
        after = _issue_ids(request.after)
        resolved = tuple(sorted(before - after))
        new = tuple(sorted(after - before))
        unchanged = tuple(sorted(before.intersection(after)))
        if request.after.release_allowed and not request.before.release_allowed:
            status = "resolved"
        elif new:
            status = "regressed"
        elif resolved:
            status = "improved"
        else:
            status = "unchanged"
        return VerifierAgentOutput(
            resolved=resolved,
            new=new,
            unchanged=unchanged,
            before_release_allowed=request.before.release_allowed,
            release_allowed=request.after.release_allowed,
            status=status,
        )


@dataclass(frozen=True)
class ReleaseRoleAgents:
    evidence: EvidenceAgent
    risk: RiskAgent
    fix_planner: FixPlannerAgent
    verifier: VerifierAgent


def _exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    label: str,
) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label} keys do not match: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _retrieval_evidence_from_dict(
    value: Mapping[str, Any],
) -> RetrievalEvidence:
    expected = {
        "evidence_id",
        "rule_id",
        "source_url",
        "local_source",
        "chunk_id",
        "retrieval_method",
        "raw_score",
        "fusion_score",
        "rerank_score",
        "text",
        "metadata",
        "relation_paths",
        "index_version",
    }
    _exact_keys(value, expected, "retrieval evidence")
    metadata = value["metadata"]
    if not isinstance(metadata, Mapping):
        raise ValueError("retrieval evidence metadata must be an object")
    raw_paths = value["relation_paths"]
    if not isinstance(raw_paths, (list, tuple)) or not all(
        isinstance(item, Mapping) for item in raw_paths
    ):
        raise ValueError("retrieval evidence relation_paths must be a list")
    index_version = value["index_version"]
    if index_version is not None and not isinstance(index_version, str):
        raise ValueError("retrieval evidence index_version must be a string or null")
    return RetrievalEvidence(
        evidence_id=str(value["evidence_id"]),
        rule_id=str(value["rule_id"]),
        source_url=str(value["source_url"]),
        local_source=str(value["local_source"]),
        chunk_id=str(value["chunk_id"]),
        retrieval_method=str(value["retrieval_method"]),
        raw_score=float(value["raw_score"]),
        fusion_score=float(value["fusion_score"]),
        rerank_score=float(value["rerank_score"]),
        text=str(value["text"]),
        metadata={str(key): str(item) for key, item in metadata.items()},
        relation_paths=tuple(_relation_path_from_dict(item) for item in raw_paths),
        index_version=index_version,
    )


def _relation_path_from_dict(value: Mapping[str, Any]) -> RelationPath:
    _exact_keys(
        value,
        {
            "node_ids",
            "edge_ids",
            "source_chunk_ids",
            "index_version",
            "hop_count",
            "path_score",
        },
        "relation path",
    )
    return RelationPath(
        node_ids=_required_string_tuple(value["node_ids"], "node_ids"),
        edge_ids=_required_string_tuple(value["edge_ids"], "edge_ids"),
        source_chunk_ids=_required_string_tuple(
            value["source_chunk_ids"], "source_chunk_ids"
        ),
        index_version=str(value["index_version"]),
        hop_count=int(value["hop_count"]),
        path_score=float(value["path_score"]),
    )


def _required_string_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{name} must contain strings")
    return tuple(value)


def _trace_with_retrieval(
    trace: ArtifactContextTrace | None,
    result: Any,
) -> ArtifactContextTrace | None:
    if trace is None:
        return None
    path_ids = tuple(sorted({
        f"{item.evidence_id}:{path.index_version}:{','.join(path.edge_ids)}"
        for item in result.evidence
        for path in item.relation_paths
    }))
    nodes = {
        node_id
        for item in result.evidence
        for path in item.relation_paths
        for node_id in path.node_ids
    }
    edges = {
        edge_id
        for item in result.evidence
        for path in item.relation_paths
        for edge_id in path.edge_ids
    }
    return replace(
        trace,
        relation_mode=result.mode_used,
        relation_fallback_reason=result.degraded_reason,
        relation_candidate_ids=tuple(sorted(
            item.evidence_id for item in result.evidence
        )),
        relation_path_ids=path_ids,
        relation_budget_usage=tuple(sorted({
            "context_characters": sum(len(item.text) for item in result.evidence),
            "edges": len(edges),
            "nodes": len(nodes),
        }.items())),
    )


def _evidence_is_sufficient(
    evidence: tuple[RetrievalEvidence, ...],
    minimum: int,
) -> bool:
    return len(evidence) >= minimum and all(
        item.evidence_id
        and item.rule_id
        and item.chunk_id
        and item.local_source
        for item in evidence
    )


def _issue_ids(review: ReleaseReviewResult) -> set[str]:
    return {
        "::".join(
            (
                result.rule_id or "NO-RULE",
                result.checker_name,
                result.title,
            )
        )
        for result in review.check_results
        if result.status in {CheckStatus.FAILED, CheckStatus.WARNING}
    }


def _normalize_action(
    project_root: Path,
    actionable_results: list[Any],
    step: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(step)
    rule_ids = [
        value for value in normalized.get("rule_ids", []) if isinstance(value, str)
    ]
    related = [result for result in actionable_results if result.rule_id in rule_ids]
    action_text = str(normalized.get("action") or normalized.get("objective") or "")
    validation = str(
        normalized.get("validation")
        or normalized.get("verification_command")
        or "重新运行 ReleaseGuard，确认相关检查已通过。"
    )
    normalized.setdefault("objective", action_text or "完成相关发布准备修复。")
    normalized.setdefault("why", "该问题会降低发布过程的可复现性或可维护性。")
    normalized.setdefault(
        "steps",
        [
            "确认检查结果指出的现象和影响范围。",
            "在建议文件中手动完成修改，并保留现有业务行为。",
            "运行验证命令，确认问题消失且没有引入新的失败。",
        ],
    )
    normalized.setdefault("example", "请根据项目现有结构完成等价配置。")
    normalized.setdefault("verification_command", validation)
    normalized.setdefault("success_criteria", "相关检查变为已通过，且现有测试保持通过。")
    normalized.setdefault("related_check_ids", [])
    normalized["suggested_files"] = _normalize_suggested_files(
        project_root,
        rule_ids,
        normalized.get("suggested_files", []),
        related,
    )
    normalized["action"] = str(normalized["objective"])
    normalized["validation"] = str(normalized["verification_command"])
    return normalized


def _normalize_suggested_files(
    project_root: Path,
    rule_ids: list[str],
    raw_files: Any,
    related_results: list[Any],
) -> list[str]:
    root = project_root.resolve()
    candidates = raw_files if isinstance(raw_files, list) else []
    paths: list[str] = []
    for value in candidates:
        if not isinstance(value, str) or not value.strip() or value == "需要人工确认":
            continue
        candidate = Path(value.strip())
        candidate = candidate if candidate.is_absolute() else root / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved != root:
            paths.append(str(resolved))
    for result in related_results:
        if not result.file_path:
            continue
        candidate = Path(result.file_path).resolve()
        if candidate != root and candidate.exists():
            paths.append(str(candidate))
    if not paths:
        targets = {
            "RG-TEST-001": root / "tests",
            "RG-TEST-006": root / "pytest.ini",
            "RG-DEPS-001": root / "requirements.txt",
            "RG-CONFIG-001": root / ".env.example",
            "RG-DOCKER-001": root / "Dockerfile",
        }
        paths.extend(str(targets[rule_id]) for rule_id in rule_ids if rule_id in targets)
    return list(dict.fromkeys(paths)) or ["需要人工确认"]


def _fallback_action(project_root: Path, result: Any, *, priority: int) -> dict[str, Any]:
    if result.rule_id == "RG-TEST-001":
        return {
            "priority": priority,
            "title": "整理 tests 测试目录",
            "objective": "把现有测试统一放入项目根目录的 tests/，确保本地和持续集成能够稳定发现。",
            "why": "测试文件分散会增加漏跑风险，也让新成员难以理解测试边界。",
            "steps": [
                "盘点项目中的 test_*.py 和 *_test.py 文件。",
                "在项目根目录创建 tests/ 并按业务模块组织测试。",
                "移动现有测试文件并修正导入、夹具和资源路径。",
                "运行 pytest 收集命令，确认预期测试全部可发现。",
                "运行完整测试，修复迁移导致的导入或路径问题。",
            ],
            "suggested_files": [str(project_root / "tests")],
            "example": "tests/\n  test_image_api.py\n  test_tasks.py",
            "verification_command": "python -m pytest --collect-only -q\npython -m pytest -q",
            "success_criteria": "tests/ 存在，预期测试均被收集，完整测试无新增失败。",
            "related_check_ids": [],
            "rule_ids": [result.rule_id],
        }
    if result.rule_id == "RG-TEST-006":
        return {
            "priority": priority,
            "title": "增加固定的 Pytest 配置",
            "objective": "固定测试发现范围和导入行为，减少本地与持续集成环境差异。",
            "why": "缺少配置时，pytest 可能因启动目录或环境差异收集不同测试。",
            "steps": [
                "确认项目当前使用 pytest.ini 还是 pyproject.toml 管理工具配置。",
                "在项目根目录新增或更新 Pytest 配置。",
                "设置 testpaths 和 python_files，使发现规则与项目测试命名一致。",
                "执行收集命令检查测试数量和路径。",
                "执行完整测试并在持续集成命令中复用相同入口。",
            ],
            "suggested_files": [str(project_root / "pytest.ini")],
            "example": "[pytest]\ntestpaths = tests\npython_files = test_*.py *_test.py\naddopts = -ra",
            "verification_command": "python -m pytest --collect-only -q\npython -m pytest -q",
            "success_criteria": "pytest 读取固定配置，收集结果稳定，完整测试无失败。",
            "related_check_ids": [],
            "rule_ids": [result.rule_id],
        }
    return {
        "priority": priority,
        "title": result.title,
        "objective": result.recommendation or result.message,
        "why": "该检查已报告警告或阻断，需要在发布前人工确认并处理。",
        "steps": [
            "阅读检查事实并确认受影响范围。",
            "按建议在具体配置或源码文件中手动修复。",
            "重新运行 ReleaseGuard 和项目测试确认结果。",
        ],
        "suggested_files": [],
        "example": "需要结合项目结构人工确认。",
        "verification_command": "python -m pytest -q",
        "success_criteria": "相关检查变为已通过，且没有新增问题。",
        "related_check_ids": [],
        "rule_ids": [result.rule_id] if result.rule_id else [],
    }
