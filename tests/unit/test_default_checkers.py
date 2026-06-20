from releaseguard_agent.checkers.common.env_example_checker import EnvExampleChecker
from releaseguard_agent.checkers.python.dependency_checker import DependencyChecker
from releaseguard_agent.checkers.python.pytest_config_checker import (
    PytestConfigChecker,
)
from releaseguard_agent.checkers.python.pytest_execution_checker import (
    PytestExecutionChecker,
)
from releaseguard_agent.checkers.python.test_structure_checker import (
    TestStructureChecker,
)
from releaseguard_agent.core.checker_runner import CheckerRunner
from releaseguard_agent.core.default_checkers import (
    build_default_python_runner,
    get_default_python_checker_names,
    get_default_python_checkers,
)
from releaseguard_agent.detectors.fastapi_detector import FastAPIDetector
from releaseguard_agent.detectors.flask_detector import FlaskDetector


def test_default_python_checkers_are_in_expected_order():
    checkers = get_default_python_checkers()

    assert [type(checker) for checker in checkers] == [
        DependencyChecker,
        EnvExampleChecker,
        TestStructureChecker,
        PytestConfigChecker,
        FastAPIDetector,
        FlaskDetector,
        PytestExecutionChecker,
    ]

    assert [checker.name for checker in checkers] == [
        "dependency_checker",
        "env_example_checker",
        "test_structure_checker",
        "pytest_config_checker",
        "fastapi_detector",
        "flask_detector",
        "pytest_execution_checker",
    ]


def test_default_python_checkers_can_exclude_pytest_execution():
    checkers = get_default_python_checkers(include_pytest_execution=False)

    assert [type(checker) for checker in checkers] == [
        DependencyChecker,
        EnvExampleChecker,
        TestStructureChecker,
        PytestConfigChecker,
        FastAPIDetector,
        FlaskDetector,
    ]

    assert "pytest_execution_checker" not in [
        checker.name for checker in checkers
    ]


def test_default_python_checker_names_match_default_checker_order():
    names = get_default_python_checker_names()

    assert names == (
        "dependency_checker",
        "env_example_checker",
        "test_structure_checker",
        "pytest_config_checker",
        "fastapi_detector",
        "flask_detector",
        "pytest_execution_checker",
    )


def test_default_python_checkers_return_fresh_instances_each_time():
    first_checkers = get_default_python_checkers()
    second_checkers = get_default_python_checkers()

    assert first_checkers is not second_checkers

    for first_checker, second_checker in zip(first_checkers, second_checkers):
        assert first_checker is not second_checker
        assert type(first_checker) is type(second_checker)


def test_build_default_python_runner_returns_checker_runner():
    runner = build_default_python_runner(include_pytest_execution=False)

    assert isinstance(runner, CheckerRunner)
    assert [checker.name for checker in runner.checkers] == [
        "dependency_checker",
        "env_example_checker",
        "test_structure_checker",
        "pytest_config_checker",
        "fastapi_detector",
        "flask_detector",
    ]


def test_default_python_runner_can_run_non_dynamic_checks(tmp_path):
    runner = build_default_python_runner(include_pytest_execution=False)

    results = runner.run(tmp_path)

    rule_ids = {result.rule_id for result in results}

    assert "RG-DEPS-001" in rule_ids
    assert "RG-CONFIG-001" in rule_ids
    assert "RG-TEST-001" in rule_ids
    assert "RG-TEST-002" in rule_ids
    assert "RG-TEST-006" in rule_ids
    assert "RG-TEST-007" in rule_ids
    assert "RG-FASTAPI-001" in rule_ids
    assert "RG-FASTAPI-002" in rule_ids
    assert "RG-FLASK-001" in rule_ids
    assert "RG-FLASK-002" in rule_ids
    assert "RG-FLASK-003" in rule_ids
    assert "RG-SEC-002" in rule_ids

    assert "RG-TEST-003" not in rule_ids
    assert "RG-TEST-004" not in rule_ids
    assert "RG-TEST-005" not in rule_ids
