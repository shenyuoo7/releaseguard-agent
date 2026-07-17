from dataclasses import dataclass
from pathlib import Path
from typing import Any

from releaseguard_agent.agents.role_agents import VerifierAgentOutput
from releaseguard_agent.services.agent_workflow_service import (
    ReleaseAgentWorkflowService,
)
from releaseguard_agent.services.release_review_service import (
    ReleaseReviewResult,
    ReleaseReviewService,
)
from releaseguard_agent.workflows import ReleaseAgentWorkflowResult


@dataclass(frozen=True)
class ReleaseVerificationResult:
    before: ReleaseReviewResult
    after_workflow: ReleaseAgentWorkflowResult
    delta: VerifierAgentOutput

    @property
    def after(self) -> ReleaseReviewResult:
        return self.after_workflow.review

    @property
    def release_allowed(self) -> bool:
        return self.delta.release_allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "delta": self.delta.to_dict(),
            "route_history": list(
                self.after_workflow.state.get("route_history", [])
            ),
        }


class ReleaseVerificationService:
    """Rescan a user-modified snapshot and compare it with a baseline scan."""

    def __init__(
        self,
        *,
        review_service: ReleaseReviewService | None = None,
        workflow_service: ReleaseAgentWorkflowService | None = None,
    ) -> None:
        self._review_service = review_service or ReleaseReviewService()
        self._workflow_service = workflow_service or ReleaseAgentWorkflowService(
            review_service=self._review_service
        )

    def verify(
        self,
        *,
        before_project_path: Path,
        after_project_path: Path,
        include_pytest_execution: bool = True,
    ) -> ReleaseVerificationResult:
        before = self._review_service.review(
            project_path=before_project_path,
            include_pytest_execution=include_pytest_execution,
        )
        after_workflow = self._workflow_service.run(
            project_path=after_project_path,
            include_pytest_execution=include_pytest_execution,
            baseline_review=before,
        )
        delta = after_workflow.verification
        if delta is None:
            raise RuntimeError("Verifier Agent did not produce a before/after delta.")
        return ReleaseVerificationResult(
            before=before,
            after_workflow=after_workflow,
            delta=delta,
        )
