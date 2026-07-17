from dataclasses import dataclass
from pathlib import Path

from releaseguard_agent.agents import (
    ReleaseRiskAnalysisAgent,
    ReleaseRiskAnalysisArtifacts,
    ReleaseRiskAnalysisContext,
    ReleaseRiskAnalysisResult,
    write_release_risk_analysis_artifacts,
)
from releaseguard_agent.llm import LLMRuntime
from releaseguard_agent.services.release_review_service import (
    ReleaseReviewResult,
    build_agent_advice_result,
)


class LLMAnalysisUnavailableError(RuntimeError):
    """Raised when optional LLM output is requested in deterministic mode."""


@dataclass(frozen=True)
class LLMReviewAnalysisResult:
    analysis: ReleaseRiskAnalysisResult
    artifacts: ReleaseRiskAnalysisArtifacts
    provider: str
    model: str | None


class LLMReviewService:
    """Add optional LLM analysis to an existing deterministic review."""

    def __init__(self, runtime: LLMRuntime) -> None:
        self._runtime = runtime

    def analyze(
        self,
        *,
        review: ReleaseReviewResult,
        output_dir: Path,
    ) -> LLMReviewAnalysisResult:
        client = self._runtime.client
        if client is None:
            raise LLMAnalysisUnavailableError(
                "LLM analysis is unavailable; deterministic review remains active."
            )
        advice = review.advice_result or build_agent_advice_result(
            project_path=review.project_path,
            results=review.check_results,
        )
        agent = ReleaseRiskAnalysisAgent(
            llm_client=client,
            model=self._runtime.model,
            temperature=0.0,
        )
        analysis = agent.analyze(ReleaseRiskAnalysisContext(advice_result=advice))
        artifacts = write_release_risk_analysis_artifacts(
            output_dir=Path(output_dir).expanduser().resolve(),
            result=analysis,
        )
        return LLMReviewAnalysisResult(
            analysis=analysis,
            artifacts=artifacts,
            provider=self._runtime.provider,
            model=self._runtime.model,
        )
