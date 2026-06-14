from pathlib import Path

import pytest

from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)


def test_base_checker_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        BaseChecker()


class DummyChecker(BaseChecker):
    name = "dummy_checker"
    description = "A checker used only for tests."

    def run(self, project_path: Path) -> list[CheckResult]:
        return [
            CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="Dummy check passed",
                message=f"Checked project path: {project_path}",
            )
        ]


def test_subclass_can_run_and_return_check_results() -> None:
    checker = DummyChecker()

    results = checker.run(Path("sample_projects/clean_python_project"))

    assert len(results) == 1
    assert results[0].checker_name == "dummy_checker"
    assert results[0].status == CheckStatus.PASSED
    assert results[0].passed is True