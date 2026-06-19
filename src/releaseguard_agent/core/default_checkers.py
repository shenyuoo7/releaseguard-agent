from releaseguard_agent.checkers.base import BaseChecker
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
from releaseguard_agent.detectors.fastapi_detector import FastAPIDetector


def get_default_python_checkers(
    *,
    include_pytest_execution: bool = True,
) -> list[BaseChecker]:
    """Return the default phase-one checker set for Python projects."""
    checkers: list[BaseChecker] = [
        DependencyChecker(),
        EnvExampleChecker(),
        TestStructureChecker(),
        PytestConfigChecker(),
        FastAPIDetector(),
    ]

    if include_pytest_execution:
        checkers.append(PytestExecutionChecker())

    return checkers


def get_default_python_checker_names(
    *,
    include_pytest_execution: bool = True,
) -> tuple[str, ...]:
    """Return checker names in default execution order."""
    return tuple(
        checker.name
        for checker in get_default_python_checkers(
            include_pytest_execution=include_pytest_execution
        )
    )


def build_default_python_runner(
    *,
    include_pytest_execution: bool = True,
) -> CheckerRunner:
    """Build a CheckerRunner with the default Python checker set."""
    return CheckerRunner(
        get_default_python_checkers(
            include_pytest_execution=include_pytest_execution
        )
    )