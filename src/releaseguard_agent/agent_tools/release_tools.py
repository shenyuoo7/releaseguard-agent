from dataclasses import dataclass
from pathlib import Path
from typing import Any

from releaseguard_agent.agents.release_risk_analysis_agent import (
    ReleaseRiskAnalysisAgent,
    ReleaseRiskAnalysisContext,
)
from releaseguard_agent.llm import LLMRuntime, OpenAIClientRequestError
from releaseguard_agent.models.retrieval_evidence import RetrievalEvidence
from releaseguard_agent.observability import ExecutionTracer
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

    def __init__(self, runtime: LLMRuntime | None = None) -> None:
        self._runtime = runtime

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
