from dataclasses import dataclass
from typing import Any

from releaseguard_agent.agent_tools import (
    EvidenceSearchTool,
    FixPlanTool,
    RiskAnalysisTool,
)
from releaseguard_agent.models.check_result import CheckStatus
from releaseguard_agent.models.retrieval_evidence import RetrievalEvidence
from releaseguard_agent.observability import ExecutionTracer
from releaseguard_agent.services.release_review_service import ReleaseReviewResult


@dataclass(frozen=True)
class EvidenceAgentInput:
    review: ReleaseReviewResult
    retrieval_mode: str = "hybrid"
    top_k: int = 5
    minimum_evidence: int = 1


@dataclass(frozen=True)
class EvidenceAgentOutput:
    evidence: tuple[RetrievalEvidence, ...]
    sufficient: bool
    supplemental_attempted: bool
    manual_review_required: bool
    degraded_reason: str | None


class EvidenceAgent:
    """Retrieve and supplement source-backed evidence for blocking findings."""

    def __init__(
        self,
        tool: EvidenceSearchTool,
        tracer: ExecutionTracer | None = None,
    ) -> None:
        self._tool = tool
        self._tracer = tracer

    def run(self, request: EvidenceAgentInput) -> EvidenceAgentOutput:
        query = " ".join(
            part
            for result in request.review.check_results
            if result.should_block_release
            for part in (result.rule_id or "", result.title, result.message)
        )
        if not query.strip():
            query = (
                "release readiness dependency tests configuration "
                "container health production safety"
            )
        initial = self._tool.invoke(
            query,
            mode=request.retrieval_mode,
            top_k=request.top_k,
            tracer=self._tracer,
        )
        combined = {item.chunk_id: item for item in initial.evidence}
        sufficient = _evidence_is_sufficient(
            tuple(combined.values()), request.minimum_evidence
        )
        supplemental_attempted = False
        if not sufficient:
            supplemental_attempted = True
            for result in request.review.check_results:
                if not result.should_block_release or not result.rule_id:
                    continue
                exact = self._tool.invoke(
                    result.rule_id,
                    mode="exact",
                    top_k=10,
                    tracer=self._tracer,
                )
                combined.update({item.chunk_id: item for item in exact.evidence})
            sufficient = _evidence_is_sufficient(
                tuple(combined.values()), request.minimum_evidence
            )
        return EvidenceAgentOutput(
            evidence=tuple(combined.values()),
            sufficient=sufficient,
            supplemental_attempted=supplemental_attempted,
            manual_review_required=not sufficient,
            degraded_reason=initial.degraded_reason,
        )


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


class FixPlannerAgent:
    """Create a manual remediation plan and ensure every blocker is covered."""

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
        blocking_rule_ids = {
            result.rule_id
            for result in request.review.check_results
            if result.should_block_release and result.rule_id
        }
        covered = {
            rule_id
            for step in raw_steps
            for rule_id in step.get("rule_ids", [])
            if isinstance(rule_id, str)
        }
        for result in request.review.check_results:
            if (
                not result.should_block_release
                or not result.rule_id
                or result.rule_id in covered
            ):
                continue
            raw_steps.append(
                {
                    "priority": len(raw_steps) + 1,
                    "title": result.title,
                    "action": result.recommendation or result.message,
                    "rule_ids": [result.rule_id],
                    "validation": (
                        "Apply the change manually, then run verification."
                    ),
                }
            )
            covered.add(result.rule_id)
        evidence_by_rule: dict[str, list[str]] = {}
        for item in request.evidence:
            evidence_by_rule.setdefault(item.rule_id, []).append(item.evidence_id)
        normalized_steps: list[dict[str, Any]] = []
        for step in raw_steps:
            normalized = dict(step)
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
            normalized_steps.append(normalized)
        all_evidence_ids = tuple(
            sorted({item.evidence_id for item in request.evidence})
        )
        return FixPlannerAgentOutput(
            steps=tuple(normalized_steps),
            covered_rule_ids=tuple(sorted(blocking_rule_ids.intersection(covered))),
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
