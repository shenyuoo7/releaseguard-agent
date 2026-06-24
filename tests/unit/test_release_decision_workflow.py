from pathlib import Path

from releaseguard_agent.agents.release_decision_agent import (
    ReleaseDecisionAgent,
)
from releaseguard_agent.agents.release_decision_synthesizer import (
    ReleaseDecisionStatus,
)
from releaseguard_agent.agents.release_decision_workflow import (
    ReleaseDecisionWorkflow,
)
from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.core.checker_runner import CheckerRunner
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RULE_INDEX_PATH = (
    PROJECT_ROOT
    / "knowledge_base"
    / "release_rules"
    / "rule_index.md"
)


class RecordingChecker(BaseChecker):
    """Test checker that records the project path it received."""

    description = "Returns predefined check results."

    def __init__(
        self,
        *,
        name: str,
        results: tuple[CheckResult, ...],
    ) -> None:
        self.name = name
        self._results = results
        self.received_project_path: Path | None = None

    def run(self, project_path: Path) -> list[CheckResult]:
        """Return predefined results while preserving object identity."""
        self.received_project_path = project_path

        return list(self._results)


def _check_result(
    *,
    rule_id: str | None,
    status: CheckStatus = CheckStatus.PASSED,
    risk_level: RiskLevel = RiskLevel.INFO,
    title: str = "Example check",
    checker_name: str = "example_checker",
) -> CheckResult:
    return CheckResult(
        checker_name=checker_name,
        status=status,
        risk_level=risk_level,
        title=title,
        message="Example message.",
        evidence=["Example evidence."],
        recommendation="Example recommendation.",
        rule_id=rule_id,
        rule_source="Example source.",
    )


def _workflow_for(
    *checkers: BaseChecker,
) -> ReleaseDecisionWorkflow:
    return ReleaseDecisionWorkflow(
        runner=CheckerRunner(checkers),
        agent=ReleaseDecisionAgent.from_rule_index(RULE_INDEX_PATH),
    )


def test_workflow_runs_supplied_checker_runner(
    tmp_path: Path,
) -> None:
    check_result = _check_result(rule_id="RG-DEPS-001")
    checker = RecordingChecker(
        name="recording_checker",
        results=(check_result,),
    )
    workflow = _workflow_for(checker)

    result = workflow.run(tmp_path)

    assert checker.received_project_path == tmp_path
    assert result.project_path == tmp_path
    assert result.check_results == (check_result,)
    assert result.decision.status == ReleaseDecisionStatus.READY


def test_workflow_passes_raw_results_into_agent(
    tmp_path: Path,
) -> None:
    check_result = _check_result(rule_id="RG-DOCKER-003")
    workflow = _workflow_for(
        RecordingChecker(
            name="recording_checker",
            results=(check_result,),
        )
    )

    result = workflow.run(tmp_path)

    assert result.decision.enriched_results[0].check_result is check_result
    assert result.decision.enriched_results[0].rule_evidence is not None
    assert result.decision.enriched_results[0].rule_evidence.rule_id == (
        "RG-DOCKER-003"
    )


def test_workflow_preserves_checker_result_order(
    tmp_path: Path,
) -> None:
    first = _check_result(
        rule_id="RG-DEPS-001",
        checker_name="first_checker",
    )
    second = _check_result(
        rule_id="RG-DOCKER-003",
        checker_name="second_checker",
    )
    third = _check_result(
        rule_id="RG-TEST-002",
        checker_name="third_checker",
    )
    workflow = _workflow_for(
        RecordingChecker(name="first_checker", results=(first, second)),
        RecordingChecker(name="second_checker", results=(third,)),
    )

    result = workflow.run(tmp_path)

    assert result.check_results == (first, second, third)
    assert result.decision.enriched_results[0].check_result is first
    assert result.decision.enriched_results[1].check_result is second
    assert result.decision.enriched_results[2].check_result is third


def test_workflow_returns_blocked_decision_for_blocking_result(
    tmp_path: Path,
) -> None:
    blocking_result = _check_result(
        rule_id="RG-DOCKER-003",
        status=CheckStatus.FAILED,
        risk_level=RiskLevel.HIGH,
        title="Dockerfile release contract failed",
    )
    workflow = _workflow_for(
        RecordingChecker(
            name="docker_checker",
            results=(blocking_result,),
        )
    )

    result = workflow.run(tmp_path)

    assert result.decision.status == ReleaseDecisionStatus.BLOCKED
    assert result.decision.release_allowed is False
    assert result.decision.blocking_rule_ids == ("RG-DOCKER-003",)


def test_workflow_does_not_mutate_original_check_results(
    tmp_path: Path,
) -> None:
    first = _check_result(rule_id="RG-DEPS-001")
    second = _check_result(
        rule_id="RG-DOCKER-003",
        status=CheckStatus.FAILED,
        risk_level=RiskLevel.HIGH,
    )
    original_data = [first.to_dict(), second.to_dict()]
    workflow = _workflow_for(
        RecordingChecker(
            name="recording_checker",
            results=(first, second),
        )
    )

    workflow.run(tmp_path)

    assert [first.to_dict(), second.to_dict()] == original_data


def test_workflow_result_can_be_converted_to_dict(
    tmp_path: Path,
) -> None:
    check_result = _check_result(rule_id="RG-DEPS-001")
    workflow = _workflow_for(
        RecordingChecker(
            name="recording_checker",
            results=(check_result,),
        )
    )

    data = workflow.run(tmp_path).to_dict()

    assert data["project_path"] == str(tmp_path)
    assert data["check_results"][0]["rule_id"] == "RG-DEPS-001"
    assert data["decision"]["status"] == "ready"
    assert data["decision"]["enriched_results"][0]["rule_evidence"][
        "rule_id"
    ] == "RG-DEPS-001"
