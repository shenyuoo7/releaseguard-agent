from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from releaseguard_agent.agents.release_decision_advice_service import (
    get_default_rule_index_path,
)
from releaseguard_agent.agents.release_decision_advisor import (
    ReleaseDecisionAdviceResult,
    ReleaseDecisionAdvisor,
)
from releaseguard_agent.agents.release_risk_analysis_agent import (
    ReleaseRiskAnalysisAgent,
    ReleaseRiskAnalysisContext,
    ReleaseRiskAnalysisResult,
)
from releaseguard_agent.agents.release_risk_analysis_writer import (
    ReleaseRiskAnalysisArtifacts,
    write_release_risk_analysis_artifacts,
)
from releaseguard_agent.core.default_checkers import build_default_python_runner
from releaseguard_agent.llm import LLMClient


@dataclass(frozen=True)
class ReleaseRiskAnalysisServiceResult:
    """Result returned by the standalone LLM risk-analysis service."""

    project_path: Path
    output_dir: Path
    include_pytest_execution: bool
    advice_result: ReleaseDecisionAdviceResult
    analysis_result: ReleaseRiskAnalysisResult
    artifacts: ReleaseRiskAnalysisArtifacts

    def to_dict(self) -> dict[str, object]:
        """Convert the service result to a plain dictionary."""
        return {
            "project_path": str(self.project_path),
            "output_dir": str(self.output_dir),
            "include_pytest_execution": self.include_pytest_execution,
            "advice_result": self.advice_result.to_dict(),
            "analysis_result": self.analysis_result.to_dict(),
            "artifacts": {
                "output_dir": str(self.artifacts.output_dir),
                "risk_analysis_markdown_path": str(
                    self.artifacts.risk_analysis_markdown_path
                ),
                "decision_json_path": str(
                    self.artifacts.decision_json_path
                ),
                "fix_plan_markdown_path": str(
                    self.artifacts.fix_plan_markdown_path
                ),
            },
        }


class ReleaseRiskAnalysisService:
    """Run default checks, analyze release risk, and write LLM artifacts."""

    def __init__(
        self,
        *,
        rule_index_path: Path,
        llm_client: LLMClient,
        source_directory: Path | None = None,
        include_pytest_execution: bool = True,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        self._rule_index_path = Path(rule_index_path)
        self._llm_client = llm_client
        self._source_directory = (
            Path(source_directory)
            if source_directory is not None
            else None
        )
        self._include_pytest_execution = include_pytest_execution
        self._model = model
        self._temperature = temperature

    @classmethod
    def from_project_defaults(
        cls,
        *,
        llm_client: LLMClient,
        include_pytest_execution: bool = True,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> "ReleaseRiskAnalysisService":
        """Create a service using the project-local rule index."""
        return cls(
            rule_index_path=get_default_rule_index_path(),
            llm_client=llm_client,
            include_pytest_execution=include_pytest_execution,
            model=model,
            temperature=temperature,
        )

    @property
    def rule_index_path(self) -> Path:
        """Return the configured rule index path."""
        return self._rule_index_path

    @property
    def include_pytest_execution(self) -> bool:
        """Return whether dynamic pytest execution is enabled."""
        return self._include_pytest_execution

    def run(
        self,
        *,
        project_path: Path,
        output_dir: Path,
        release_report_markdown: str | None = None,
        release_checklist_markdown: str | None = None,
        trace_payload: Mapping[str, Any] | None = None,
    ) -> ReleaseRiskAnalysisServiceResult:
        """Run release-risk analysis and write LLM Agent artifacts."""
        normalized_project_path = Path(project_path)
        normalized_output_dir = Path(output_dir)

        runner = build_default_python_runner(
            include_pytest_execution=self._include_pytest_execution
        )
        advisor = ReleaseDecisionAdvisor.from_rule_index(
            runner=runner,
            index_path=self._rule_index_path,
            source_directory=self._source_directory,
        )
        advice_result = advisor.advise(normalized_project_path)

        agent = ReleaseRiskAnalysisAgent(
            llm_client=self._llm_client,
            model=self._model,
            temperature=self._temperature,
        )
        analysis_result = agent.analyze(
            ReleaseRiskAnalysisContext(
                advice_result=advice_result,
                release_report_markdown=release_report_markdown,
                release_checklist_markdown=release_checklist_markdown,
                trace_payload=trace_payload or {},
            )
        )
        artifacts = write_release_risk_analysis_artifacts(
            output_dir=normalized_output_dir,
            result=analysis_result,
        )

        return ReleaseRiskAnalysisServiceResult(
            project_path=normalized_project_path,
            output_dir=normalized_output_dir,
            include_pytest_execution=self._include_pytest_execution,
            advice_result=advice_result,
            analysis_result=analysis_result,
            artifacts=artifacts,
        )
