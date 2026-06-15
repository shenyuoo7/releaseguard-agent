from collections.abc import Iterable
from pathlib import Path
from typing import Any

from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)


class CheckerRunner:
    """Run release-readiness checkers and aggregate their results."""

    def __init__(self, checkers: Iterable[BaseChecker] | None = None) -> None:
        """Initialize the runner with an optional checker sequence."""
        self._checkers = list(checkers or [])

    @property
    def checkers(self) -> tuple[BaseChecker, ...]:
        """Return registered checkers as an immutable tuple."""
        return tuple(self._checkers)

    def add_checker(self, checker: BaseChecker) -> None:
        """Register one checker."""
        self._checkers.append(checker)

    def run(self, project_path: Path) -> list[CheckResult]:
        """Run all registered checkers against a target project path."""
        normalized_project_path = Path(project_path)
        results: list[CheckResult] = []

        for checker in self._checkers:
            try:
                checker_results = checker.run(normalized_project_path)
                results.extend(
                    self._validate_checker_results(
                        checker=checker,
                        project_path=normalized_project_path,
                        checker_results=checker_results,
                    )
                )
            except Exception as error:
                results.append(
                    self._build_checker_error_result(
                        checker=checker,
                        project_path=normalized_project_path,
                        error=error,
                    )
                )

        return results

    def _validate_checker_results(
        self,
        checker: BaseChecker,
        project_path: Path,
        checker_results: Any,
    ) -> list[CheckResult]:
        """Validate that a checker returned a list of CheckResult objects."""
        if not isinstance(checker_results, list):
            return [
                self._build_invalid_return_result(
                    checker=checker,
                    project_path=project_path,
                    checker_results=checker_results,
                    invalid_item_types=[],
                )
            ]

        invalid_item_types = [
            type(item).__name__
            for item in checker_results
            if not isinstance(item, CheckResult)
        ]

        if invalid_item_types:
            return [
                self._build_invalid_return_result(
                    checker=checker,
                    project_path=project_path,
                    checker_results=checker_results,
                    invalid_item_types=invalid_item_types,
                )
            ]

        return checker_results

    def _build_checker_error_result(
        self,
        checker: BaseChecker,
        project_path: Path,
        error: Exception,
    ) -> CheckResult:
        """Build a failed result when a checker raises an exception."""
        checker_name = self._checker_name(checker)

        return CheckResult(
            checker_name=checker_name,
            status=CheckStatus.FAILED,
            risk_level=RiskLevel.HIGH,
            title="Checker execution failed",
            message=f"{checker_name} raised an exception during execution.",
            evidence=[
                f"Checker: {checker_name}",
                f"Exception type: {type(error).__name__}",
                f"Exception message: {error}",
            ],
            recommendation=(
                "Fix the checker implementation or handle this project shape "
                "before trusting the release-readiness result."
            ),
            rule_id=None,
            rule_source="ReleaseGuard runner policy",
            file_path=str(project_path),
            metadata={
                "checker_name": checker_name,
                "checker_description": self._checker_description(checker),
                "exception_type": type(error).__name__,
                "exception_message": str(error),
                "support_level": "releaseguard-default",
                "blocking_policy": "block",
                "evidence_type": "runner_exception",
            },
        )

    def _build_invalid_return_result(
        self,
        checker: BaseChecker,
        project_path: Path,
        checker_results: Any,
        invalid_item_types: list[str],
    ) -> CheckResult:
        """Build a failed result when a checker returns invalid data."""
        checker_name = self._checker_name(checker)
        returned_type = type(checker_results).__name__

        evidence = [
            f"Checker: {checker_name}",
            f"Returned type: {returned_type}",
            "Expected return type: list[CheckResult]",
        ]

        if invalid_item_types:
            evidence.append(
                "Invalid item types: " + ", ".join(invalid_item_types)
            )

        return CheckResult(
            checker_name=checker_name,
            status=CheckStatus.FAILED,
            risk_level=RiskLevel.HIGH,
            title="Checker returned invalid result data",
            message=(
                f"{checker_name} did not return a valid list of CheckResult "
                "objects."
            ),
            evidence=evidence,
            recommendation=(
                "Update the checker so run(project_path) returns "
                "list[CheckResult]."
            ),
            rule_id=None,
            rule_source="ReleaseGuard runner policy",
            file_path=str(project_path),
            metadata={
                "checker_name": checker_name,
                "checker_description": self._checker_description(checker),
                "returned_type": returned_type,
                "invalid_item_types": invalid_item_types,
                "support_level": "releaseguard-default",
                "blocking_policy": "block",
                "evidence_type": "invalid_checker_return",
            },
        )

    def _checker_name(self, checker: BaseChecker) -> str:
        """Return a stable checker name for reports."""
        return str(getattr(checker, "name", checker.__class__.__name__))

    def _checker_description(self, checker: BaseChecker) -> str:
        """Return a checker description for metadata."""
        return str(getattr(checker, "description", ""))