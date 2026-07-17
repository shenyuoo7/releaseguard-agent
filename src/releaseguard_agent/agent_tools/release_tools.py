from dataclasses import dataclass
from pathlib import Path
from typing import Any

from releaseguard_agent.agents import (
    ReleaseRiskAnalysisAgent,
    ReleaseRiskAnalysisContext,
)
from releaseguard_agent.llm import LLMRuntime
from releaseguard_agent.models.retrieval_evidence import RetrievalEvidence
from releaseguard_agent.rag import RetrievalResult, RuleRetrievalService
from releaseguard_agent.services.release_review_service import (
    ReleaseReviewResult,
    ReleaseReviewService,
    build_agent_advice_result,
)


class ScanProjectTool:
    """Agent-callable wrapper around the shared deterministic review service."""

    def __init__(self, service: ReleaseReviewService | None = None) -> None:
        self._service = service or ReleaseReviewService()

    def invoke(
        self,
        project_path: Path,
        *,
        include_pytest_execution: bool,
    ) -> ReleaseReviewResult:
        return self._service.review(
            project_path=project_path,
            include_pytest_execution=include_pytest_execution,
        )


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
    ) -> RetrievalResult:
        return self._service.retrieve(query, mode=mode, top_k=top_k)


@dataclass(frozen=True)
class RiskToolResult:
    payload: dict[str, Any]
    llm_attempted: bool
    llm_failed: bool
    error_type: str | None = None


class RiskAnalysisTool:
    """Produce guarded risk analysis with deterministic LLM fallback."""

    def __init__(self, runtime: LLMRuntime | None = None) -> None:
        self._runtime = runtime

    def invoke(
        self,
        review: ReleaseReviewResult,
        evidence: tuple[RetrievalEvidence, ...],
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
            trace_payload={
                "retrieval_evidence": [item.to_dict() for item in evidence],
            },
        )
        try:
            result = ReleaseRiskAnalysisAgent(
                llm_client=runtime.client,
                model=runtime.model,
                temperature=0.0,
            ).analyze(context)
        except Exception as exc:
            return RiskToolResult(
                payload=_deterministic_risk_payload(review, evidence),
                llm_attempted=True,
                llm_failed=True,
                error_type=type(exc).__name__,
            )
        payload = result.analysis.to_dict()
        payload["analysis_source"] = "llm"
        payload["evidence_ids"] = [item.evidence_id for item in evidence]
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
            f"{len(blocking)} deterministic blocking finding(s)."
            if blocking
            else "No deterministic blocking findings."
        ),
        "release_allowed": review.release_allowed,
        "release_status": "release" if review.release_allowed else "block",
        "evidence_ids": [item.evidence_id for item in evidence],
        "fix_plan": [],
    }
