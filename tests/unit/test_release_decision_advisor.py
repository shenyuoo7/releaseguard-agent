from pathlib import Path

from releaseguard_agent.agents.release_decision_advisor import (
    ReleaseDecisionAdvisor,
)
from releaseguard_agent.agents.release_decision_synthesizer import (
    ReleaseDecisionStatus,
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


def _advisor_for(
    *checkers: BaseChecker,
) -> ReleaseDecisionAdvisor:
    return ReleaseDecisionAdvisor.from_rule_index(
        runner=CheckerRunner(checkers),
        index_path=RULE_INDEX_PATH,
    )


def test_advisor_runs_workflow_and_returns_explanation(
    tmp_path: Path,
) -> None:
    check_result = _check_result(rule_id="RG-DEPS-001")
    checker = RecordingChecker(
        name="dependency_checker",
        results=(check_result,),
    )
    advisor = _advisor_for(checker)

    result = advisor.advise(tmp_path)

    assert checker.received_project_path == tmp_path
    assert result.project_path == tmp_path
    assert result.check_results == (check_result,)
    assert result.decision.status == ReleaseDecisionStatus.READY
    assert result.explanation.status == ReleaseDecisionStatus.READY
    assert "Release ready" in result.explanation.headline


def test_advisor_explains_blocked_decision(
    tmp_path: Path,
) -> None:
    blocking_result = _check_result(
        rule_id="RG-DOCKER-003",
        status=CheckStatus.FAILED,
        risk_level=RiskLevel.HIGH,
        title="Dockerfile release contract failed",
    )
    advisor = _advisor_for(
        RecordingChecker(
            name="docker_checker",
            results=(blocking_result,),
        )
    )

    result = advisor.advise(tmp_path)

    assert result.decision.status == ReleaseDecisionStatus.BLOCKED
    assert result.decision.release_allowed is False
    assert result.explanation.release_allowed is False
    assert len(result.explanation.blocking_findings) == 1
    assert result.explanation.blocking_findings[0].rule_id == (
        "RG-DOCKER-003"
    )


def test_advisor_explains_missing_rule_evidence(
    tmp_path: Path,
) -> None:
    unknown_result = _check_result(
        rule_id="RG-UNKNOWN-999",
        title="Unknown mapped check",
    )
    advisor = _advisor_for(
        RecordingChecker(
            name="unknown_checker",
            results=(unknown_result,),
        )
    )

    result = advisor.advise(tmp_path)

    assert result.decision.status == ReleaseDecisionStatus.REVIEW_RECOMMENDED
    assert result.decision.missing_rule_evidence_count == 1
    assert len(result.explanation.missing_evidence_findings) == 1
    assert result.explanation.missing_evidence_findings[0].rule_id == (
        "RG-UNKNOWN-999"
    )


def test_advisor_preserves_checker_result_order(
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
    advisor = _advisor_for(
        RecordingChecker(name="first_checker", results=(first, second)),
        RecordingChecker(name="second_checker", results=(third,)),
    )

    result = advisor.advise(tmp_path)

    assert result.check_results == (first, second, third)
    assert result.decision.enriched_results[0].check_result is first
    assert result.decision.enriched_results[1].check_result is second
    assert result.decision.enriched_results[2].check_result is third


def test_advisor_does_not_mutate_original_check_results(
    tmp_path: Path,
) -> None:
    first = _check_result(rule_id="RG-DEPS-001")
    second = _check_result(
        rule_id="RG-DOCKER-003",
        status=CheckStatus.FAILED,
        risk_level=RiskLevel.HIGH,
    )
    original_data = [first.to_dict(), second.to_dict()]
    advisor = _advisor_for(
        RecordingChecker(
            name="recording_checker",
            results=(first, second),
        )
    )

    advisor.advise(tmp_path)

    assert [first.to_dict(), second.to_dict()] == original_data


def test_advice_result_can_be_converted_to_dict(
    tmp_path: Path,
) -> None:
    check_result = _check_result(rule_id="RG-DEPS-001")
    advisor = _advisor_for(
        RecordingChecker(
            name="dependency_checker",
            results=(check_result,),
        )
    )

    data = advisor.advise(tmp_path).to_dict()

    assert data["workflow_result"]["project_path"] == str(tmp_path)
    assert data["workflow_result"]["decision"]["status"] == "ready"
    assert data["explanation"]["status"] == "ready"
    assert data["explanation"]["markdown"].startswith("# Release Decision")
