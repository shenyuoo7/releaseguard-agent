from dataclasses import dataclass
from pathlib import Path

from releaseguard_agent.agents.release_decision_advice_writer import (
    ReleaseDecisionAdviceArtifacts,
    write_advice_artifacts,
)
from releaseguard_agent.agents.release_decision_advisor import (
    ReleaseDecisionAdviceResult,
    ReleaseDecisionAdvisor,
)
from releaseguard_agent.core.default_checkers import build_default_python_runner


@dataclass(frozen=True)
class ReleaseDecisionAdviceServiceResult:
    """Result returned by the standalone Agent advice service."""

    project_path: Path
    output_dir: Path
    include_pytest_execution: bool
    advice_result: ReleaseDecisionAdviceResult
    artifacts: ReleaseDecisionAdviceArtifacts

    def to_dict(self) -> dict[str, object]:
        """Convert the service result to a plain dictionary."""
        return {
            "project_path": str(self.project_path),
            "output_dir": str(self.output_dir),
            "include_pytest_execution": self.include_pytest_execution,
            "advice_result": self.advice_result.to_dict(),
            "artifacts": {
                "output_dir": str(self.artifacts.output_dir),
                "markdown_path": str(self.artifacts.markdown_path),
                "json_path": str(self.artifacts.json_path),
            },
        }


class ReleaseDecisionAdviceService:
    """Run default checks and write deterministic Agent advice artifacts."""

    def __init__(
        self,
        *,
        rule_index_path: Path,
        source_directory: Path | None = None,
        include_pytest_execution: bool = True,
    ) -> None:
        self._rule_index_path = Path(rule_index_path)
        self._source_directory = (
            Path(source_directory)
            if source_directory is not None
            else None
        )
        self._include_pytest_execution = include_pytest_execution

    @classmethod
    def from_project_defaults(
        cls,
        *,
        include_pytest_execution: bool = True,
    ) -> "ReleaseDecisionAdviceService":
        """Create a service using the project-local rule index."""
        return cls(
            rule_index_path=get_default_rule_index_path(),
            include_pytest_execution=include_pytest_execution,
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
    ) -> ReleaseDecisionAdviceServiceResult:
        """Run default checks and write Agent advice artifacts."""
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
        artifacts = write_advice_artifacts(
            output_dir=normalized_output_dir,
            advice_result=advice_result,
        )

        return ReleaseDecisionAdviceServiceResult(
            project_path=normalized_project_path,
            output_dir=normalized_output_dir,
            include_pytest_execution=self._include_pytest_execution,
            advice_result=advice_result,
            artifacts=artifacts,
        )


def get_default_rule_index_path() -> Path:
    """Return the project-local release rule index path."""
    return (
        Path(__file__).resolve().parents[3]
        / "knowledge_base"
        / "release_rules"
        / "rule_index.md"
    )
