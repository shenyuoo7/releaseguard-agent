from dataclasses import dataclass
from pathlib import Path

from releaseguard_agent.agents.release_decision_explainer import (
    ReleaseDecisionExplainer,
    ReleaseDecisionExplanation,
)
from releaseguard_agent.agents.release_decision_synthesizer import (
    ReleaseDecision,
)
from releaseguard_agent.agents.release_decision_workflow import (
    ReleaseDecisionWorkflow,
    ReleaseDecisionWorkflowResult,
)
from releaseguard_agent.core.checker_runner import CheckerRunner
from releaseguard_agent.models.check_result import CheckResult


@dataclass(frozen=True)
class ReleaseDecisionAdviceResult:
    """Project-level Agent advice result."""

    workflow_result: ReleaseDecisionWorkflowResult
    explanation: ReleaseDecisionExplanation

    @property
    def project_path(self) -> Path:
        """Return the analyzed project path."""
        return self.workflow_result.project_path

    @property
    def check_results(self) -> tuple[CheckResult, ...]:
        """Return raw checker results."""
        return self.workflow_result.check_results

    @property
    def decision(self) -> ReleaseDecision:
        """Return the synthesized release decision."""
        return self.workflow_result.decision

    def to_dict(self) -> dict[str, object]:
        """Convert the advice result to a plain dictionary."""
        return {
            "workflow_result": self.workflow_result.to_dict(),
            "explanation": self.explanation.to_dict(),
        }


class ReleaseDecisionAdvisor:
    """Compose workflow execution and deterministic explanation."""

    def __init__(
        self,
        *,
        workflow: ReleaseDecisionWorkflow,
        explainer: ReleaseDecisionExplainer | None = None,
    ) -> None:
        self._workflow = workflow
        self._explainer = explainer or ReleaseDecisionExplainer()

    @classmethod
    def from_rule_index(
        cls,
        *,
        runner: CheckerRunner,
        index_path: Path,
        source_directory: Path | None = None,
    ) -> "ReleaseDecisionAdvisor":
        """Create an advisor from a runner and local rule index."""
        return cls(
            workflow=ReleaseDecisionWorkflow.from_rule_index(
                runner=runner,
                index_path=index_path,
                source_directory=source_directory,
            )
        )

    def advise(self, project_path: Path) -> ReleaseDecisionAdviceResult:
        """Run the workflow and explain the synthesized decision."""
        workflow_result = self._workflow.run(project_path)
        explanation = self._explainer.explain(workflow_result.decision)

        return ReleaseDecisionAdviceResult(
            workflow_result=workflow_result,
            explanation=explanation,
        )
