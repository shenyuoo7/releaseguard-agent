from pathlib import Path

from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.core.checker_runner import CheckerRunner
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)


class StaticResultChecker(BaseChecker):
    """Test checker that returns predefined results."""

    description = "Returns predefined check results."

    def __init__(self, name: str, rule_id: str) -> None:
        self.name = name
        self.rule_id = rule_id
        self.received_project_path: Path | None = None

    def run(self, project_path: Path) -> list[CheckResult]:
        """Return one passing result."""
        self.received_project_path = project_path

        return [
            CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="Static check passed",
                message="The static checker returned a passing result.",
                evidence=[f"Project path: {project_path}"],
                recommendation=None,
                rule_id=self.rule_id,
                rule_source="Test rule source",
                file_path=str(project_path),
                metadata={
                    "support_level": "source-backed",
                    "blocking_policy": "info",
                    "evidence_type": "test_result",
                },
            )
        ]


class MultiResultChecker(BaseChecker):
    """Test checker that returns more than one result."""

    name = "multi_result_checker"
    description = "Returns multiple check results."

    def run(self, project_path: Path) -> list[CheckResult]:
        """Return two results."""
        return [
            CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="First result",
                message="First result message.",
                rule_id="RG-TEST-A",
                file_path=str(project_path),
            ),
            CheckResult(
                checker_name=self.name,
                status=CheckStatus.WARNING,
                risk_level=RiskLevel.MEDIUM,
                title="Second result",
                message="Second result message.",
                rule_id="RG-TEST-B",
                file_path=str(project_path),
            ),
        ]


class RaisingChecker(BaseChecker):
    """Test checker that raises an exception."""

    name = "raising_checker"
    description = "Raises an exception during execution."

    def run(self, project_path: Path) -> list[CheckResult]:
        """Raise a runtime error."""
        raise RuntimeError("simulated checker failure")


class InvalidReturnChecker(BaseChecker):
    """Test checker that returns invalid data."""

    name = "invalid_return_checker"
    description = "Returns invalid data instead of check results."

    def run(self, project_path: Path):
        """Return invalid data."""
        return None


class InvalidListItemChecker(BaseChecker):
    """Test checker that returns a list with invalid items."""

    name = "invalid_list_item_checker"
    description = "Returns a list containing invalid items."

    def run(self, project_path: Path):
        """Return a list with an invalid item."""
        return ["not a check result"]


def test_checker_runner_returns_empty_list_when_no_checkers_exist(tmp_path):
    runner = CheckerRunner()

    results = runner.run(tmp_path)

    assert results == []


def test_checker_runner_runs_checkers_in_order(tmp_path):
    first_checker = StaticResultChecker(
        name="first_checker",
        rule_id="RG-FIRST-001",
    )
    second_checker = StaticResultChecker(
        name="second_checker",
        rule_id="RG-SECOND-001",
    )
    runner = CheckerRunner([first_checker, second_checker])

    results = runner.run(tmp_path)

    assert [result.rule_id for result in results] == [
        "RG-FIRST-001",
        "RG-SECOND-001",
    ]
    assert [result.checker_name for result in results] == [
        "first_checker",
        "second_checker",
    ]


def test_checker_runner_passes_project_path_to_checkers(tmp_path):
    checker = StaticResultChecker(
        name="path_checker",
        rule_id="RG-PATH-001",
    )
    runner = CheckerRunner([checker])

    runner.run(tmp_path)

    assert checker.received_project_path == tmp_path


def test_checker_runner_can_add_checker_after_creation(tmp_path):
    checker = StaticResultChecker(
        name="added_checker",
        rule_id="RG-ADDED-001",
    )
    runner = CheckerRunner()

    runner.add_checker(checker)
    results = runner.run(tmp_path)

    assert len(runner.checkers) == 1
    assert results[0].rule_id == "RG-ADDED-001"


def test_checker_runner_aggregates_multiple_results_from_one_checker(tmp_path):
    runner = CheckerRunner([MultiResultChecker()])

    results = runner.run(tmp_path)

    assert len(results) == 2
    assert [result.rule_id for result in results] == [
        "RG-TEST-A",
        "RG-TEST-B",
    ]
    assert results[0].status == CheckStatus.PASSED
    assert results[1].status == CheckStatus.WARNING


def test_checker_runner_converts_checker_exception_to_failed_result(tmp_path):
    runner = CheckerRunner([RaisingChecker()])

    results = runner.run(tmp_path)

    assert len(results) == 1
    result = results[0]

    assert result.checker_name == "raising_checker"
    assert result.status == CheckStatus.FAILED
    assert result.risk_level == RiskLevel.HIGH
    assert result.should_block_release is True
    assert result.rule_id is None
    assert result.rule_source == "ReleaseGuard runner policy"
    assert result.metadata["exception_type"] == "RuntimeError"
    assert result.metadata["blocking_policy"] == "block"


def test_checker_runner_continues_after_checker_exception(tmp_path):
    first_checker = StaticResultChecker(
        name="first_checker",
        rule_id="RG-FIRST-001",
    )
    second_checker = StaticResultChecker(
        name="second_checker",
        rule_id="RG-SECOND-001",
    )
    runner = CheckerRunner([first_checker, RaisingChecker(), second_checker])

    results = runner.run(tmp_path)

    assert [result.checker_name for result in results] == [
        "first_checker",
        "raising_checker",
        "second_checker",
    ]
    assert [result.status for result in results] == [
        CheckStatus.PASSED,
        CheckStatus.FAILED,
        CheckStatus.PASSED,
    ]


def test_checker_runner_reports_invalid_non_list_return_value(tmp_path):
    runner = CheckerRunner([InvalidReturnChecker()])

    results = runner.run(tmp_path)

    assert len(results) == 1
    result = results[0]

    assert result.checker_name == "invalid_return_checker"
    assert result.status == CheckStatus.FAILED
    assert result.risk_level == RiskLevel.HIGH
    assert result.metadata["returned_type"] == "NoneType"
    assert result.metadata["evidence_type"] == "invalid_checker_return"


def test_checker_runner_reports_invalid_items_inside_result_list(tmp_path):
    runner = CheckerRunner([InvalidListItemChecker()])

    results = runner.run(tmp_path)

    assert len(results) == 1
    result = results[0]

    assert result.checker_name == "invalid_list_item_checker"
    assert result.status == CheckStatus.FAILED
    assert result.risk_level == RiskLevel.HIGH
    assert result.metadata["returned_type"] == "list"
    assert result.metadata["invalid_item_types"] == ["str"]