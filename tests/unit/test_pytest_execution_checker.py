from releaseguard_agent.checkers.python.pytest_execution_checker import (
    PytestExecutionChecker,
)
from releaseguard_agent.models.check_result import CheckStatus, RiskLevel


def get_result_by_rule_id(results, rule_id):
    for result in results:
        if result.rule_id == rule_id:
            return result

    raise AssertionError(f"Result with rule_id {rule_id} was not found.")


def create_test_file(project_path, content):
    tests_dir = project_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_sample.py"
    test_file.write_text(content, encoding="utf-8")
    return test_file


def test_pytest_execution_checker_passes_when_tests_collect_and_pass(tmp_path):
    create_test_file(
        tmp_path,
        "def test_sample():\n    assert True\n",
    )

    checker = PytestExecutionChecker()

    results = checker.run(tmp_path)

    collected_tests_result = get_result_by_rule_id(results, "RG-TEST-003")
    collect_command_result = get_result_by_rule_id(results, "RG-TEST-004")
    run_command_result = get_result_by_rule_id(results, "RG-TEST-005")

    assert collected_tests_result.status == CheckStatus.PASSED
    assert collected_tests_result.metadata["collected_test_count"] == 1

    assert collect_command_result.status == CheckStatus.PASSED
    assert collect_command_result.metadata["exit_code"] == 0

    assert run_command_result.status == CheckStatus.PASSED
    assert run_command_result.metadata["exit_code"] == 0


def test_pytest_execution_checker_fails_when_no_tests_can_be_collected(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    helper_file = tests_dir / "helper.py"
    helper_file.write_text("VALUE = 1\n", encoding="utf-8")

    checker = PytestExecutionChecker()

    results = checker.run(tmp_path)

    collected_tests_result = get_result_by_rule_id(results, "RG-TEST-003")
    collect_command_result = get_result_by_rule_id(results, "RG-TEST-004")
    run_command_result = get_result_by_rule_id(results, "RG-TEST-005")

    assert collected_tests_result.status == CheckStatus.FAILED
    assert collected_tests_result.risk_level == RiskLevel.HIGH
    assert collected_tests_result.should_block_release is True

    assert collect_command_result.status == CheckStatus.FAILED
    assert collect_command_result.risk_level == RiskLevel.HIGH

    assert run_command_result.status == CheckStatus.FAILED
    assert run_command_result.risk_level == RiskLevel.CRITICAL


def test_pytest_execution_checker_fails_when_test_run_fails(tmp_path):
    create_test_file(
        tmp_path,
        "def test_sample():\n    assert False\n",
    )

    checker = PytestExecutionChecker()

    results = checker.run(tmp_path)

    collected_tests_result = get_result_by_rule_id(results, "RG-TEST-003")
    collect_command_result = get_result_by_rule_id(results, "RG-TEST-004")
    run_command_result = get_result_by_rule_id(results, "RG-TEST-005")

    assert collected_tests_result.status == CheckStatus.PASSED
    assert collect_command_result.status == CheckStatus.PASSED

    assert run_command_result.status == CheckStatus.FAILED
    assert run_command_result.risk_level == RiskLevel.CRITICAL
    assert run_command_result.failed is True
    assert run_command_result.should_block_release is True
    assert run_command_result.recommendation is not None


def test_pytest_execution_checker_records_command_metadata(tmp_path):
    create_test_file(
        tmp_path,
        "def test_sample():\n    assert True\n",
    )

    checker = PytestExecutionChecker()

    results = checker.run(tmp_path)

    run_command_result = get_result_by_rule_id(results, "RG-TEST-005")

    assert run_command_result.metadata["command"] == "python -m pytest -q"
    assert run_command_result.metadata["support_level"] == "source-backed"
    assert run_command_result.metadata["blocking_policy"] == "block"
    assert run_command_result.metadata["evidence_type"] == "command_result"
    assert isinstance(run_command_result.metadata["duration_seconds"], float)


def test_pytest_execution_checker_returns_three_rule_results(tmp_path):
    create_test_file(
        tmp_path,
        "def test_sample():\n    assert True\n",
    )

    checker = PytestExecutionChecker()

    results = checker.run(tmp_path)

    rule_ids = {result.rule_id for result in results}

    assert rule_ids == {
        "RG-TEST-003",
        "RG-TEST-004",
        "RG-TEST-005",
    }