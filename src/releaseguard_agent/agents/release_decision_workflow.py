from dataclasses import dataclass
from pathlib import Path

from releaseguard_agent.agents.release_decision_agent import (
    ReleaseDecisionAgent,
)
from releaseguard_agent.agents.release_decision_synthesizer import (
    ReleaseDecision,
)
from releaseguard_agent.core.checker_runner import CheckerRunner
from releaseguard_agent.models.check_result import CheckResult


@dataclass(frozen=True)
class ReleaseDecisionWorkflowResult:
    """Result returned by the deterministic release decision workflow."""

    project_path: Path
    check_results: tuple[CheckResult, ...]
    decision: ReleaseDecision

    def to_dict(self) -> dict[str, object]:
        """Convert the workflow result to a plain dictionary."""
        return {
            "project_path": str(self.project_path),
            "check_results": [
                check_result.to_dict()
                for check_result in self.check_results
            ],
            "decision": self.decision.to_dict(),
        }


class ReleaseDecisionWorkflow:
    """Run checkers and produce a deterministic Agent release decision."""

    def __init__(
        self,
        *,
        runner: CheckerRunner,
        agent: ReleaseDecisionAgent,
    ) -> None:
        self._runner = runner
        self._agent = agent

    @classmethod
    def from_rule_index(
        cls,
        *,
        runner: CheckerRunner,
        index_path: Path,
        source_directory: Path | None = None,
    ) -> "ReleaseDecisionWorkflow":
        """Create a workflow from a checker runner and rule index."""
        return cls(
            runner=runner,
            agent=ReleaseDecisionAgent.from_rule_index(
                index_path=index_path,
                source_directory=source_directory,
            ),
        )

    def run(self, project_path: Path) -> ReleaseDecisionWorkflowResult:
        """Run the supplied checker runner and synthesize a decision."""
        normalized_project_path = Path(project_path)
        check_results = tuple(self._runner.run(normalized_project_path))
        decision = self._agent.decide(check_results)

        return ReleaseDecisionWorkflowResult(
            project_path=normalized_project_path,
            check_results=check_results,
            decision=decision,
        )
