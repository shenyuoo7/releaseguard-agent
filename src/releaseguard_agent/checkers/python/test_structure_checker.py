from pathlib import Path

from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)


class TestStructureChecker(BaseChecker):
    """Check whether a Python project has a pytest-discoverable test structure."""

    name = "test_structure_checker"
    description = "Checks whether the project has tests and pytest-style test files."

    tests_directory_name = "tests"

    ignored_directories: tuple[str, ...] = (
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "outputs",
        "build",
        "dist",
        "site-packages",
    )

    def run(self, project_path: Path) -> list[CheckResult]:
        """Run test structure checks against a target project path."""
        return [
            self._check_tests_directory(project_path),
            self._check_test_files(project_path),
        ]

    def _check_tests_directory(self, project_path: Path) -> CheckResult:
        """Check whether the project has a tests directory."""
        tests_dir = project_path / self.tests_directory_name

        if tests_dir.is_dir():
            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="Tests directory found",
                message="The project has a tests directory.",
                evidence=[f"Found tests directory: {self.tests_directory_name}/"],
                recommendation=None,
                rule_id="RG-TEST-001",
                rule_source=(
                    "pytest Good Integration Practices; "
                    "ReleaseGuard default policy"
                ),
                file_path=str(tests_dir),
                metadata={
                    "checked_directory": self.tests_directory_name,
                    "found_directory": self.tests_directory_name,
                    "support_level": "releaseguard-default",
                    "blocking_policy": "warn",
                    "evidence_type": "directory_exists",
                },
            )

        return CheckResult(
            checker_name=self.name,
            status=CheckStatus.WARNING,
            risk_level=RiskLevel.MEDIUM,
            title="Tests directory missing",
            message="The project does not have a tests directory in the project root.",
            evidence=[
                f"Checked for directory: {self.tests_directory_name}/",
                "No tests directory was found in the project root.",
            ],
            recommendation=(
                "Create a tests/ directory to keep release-readiness tests "
                "easy to discover and run."
            ),
            rule_id="RG-TEST-001",
            rule_source=(
                "pytest Good Integration Practices; "
                "ReleaseGuard default policy"
            ),
            file_path=str(project_path),
            metadata={
                "checked_directory": self.tests_directory_name,
                "found_directory": None,
                "support_level": "releaseguard-default",
                "blocking_policy": "warn",
                "evidence_type": "directory_exists",
            },
        )

    def _check_test_files(self, project_path: Path) -> CheckResult:
        """Check whether pytest-discoverable test files exist."""
        test_files = self._find_test_files(project_path)

        if test_files:
            relative_paths = [
                str(self._safe_relative_path(test_file, project_path))
                for test_file in test_files
            ]

            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="Pytest-discoverable test files found",
                message="The project has pytest-discoverable test files.",
                evidence=[
                    f"Found test file: {relative_path}"
                    for relative_path in relative_paths[:10]
                ],
                recommendation=None,
                rule_id="RG-TEST-002",
                rule_source="pytest Good Integration Practices",
                file_path=str(project_path),
                metadata={
                    "test_file_patterns": ["test_*.py", "*_test.py"],
                    "found_file_count": len(test_files),
                    "found_files": relative_paths,
                    "support_level": "source-backed",
                    "blocking_policy": "block",
                    "evidence_type": "file_glob_matches",
                },
            )

        return CheckResult(
            checker_name=self.name,
            status=CheckStatus.FAILED,
            risk_level=RiskLevel.HIGH,
            title="Pytest-discoverable test files missing",
            message=(
                "The project does not have pytest-discoverable test files. "
                "pytest normally discovers files named test_*.py or *_test.py."
            ),
            evidence=[
                "Checked test file patterns: test_*.py, *_test.py",
                "No pytest-discoverable test files were found.",
            ],
            recommendation=(
                "Add at least one pytest-discoverable test file, such as "
                "tests/test_example.py."
            ),
            rule_id="RG-TEST-002",
            rule_source="pytest Good Integration Practices",
            file_path=str(project_path),
            metadata={
                "test_file_patterns": ["test_*.py", "*_test.py"],
                "found_file_count": 0,
                "found_files": [],
                "support_level": "source-backed",
                "blocking_policy": "block",
                "evidence_type": "file_glob_matches",
            },
        )

    def _find_test_files(self, project_path: Path) -> list[Path]:
        """Return pytest-discoverable test files found under the project."""
        test_files = [
            python_file
            for python_file in project_path.rglob("*.py")
            if not self._should_ignore_path(python_file)
            and self._is_pytest_test_file(python_file)
        ]

        return sorted(test_files, key=lambda path: str(path))

    def _is_pytest_test_file(self, path: Path) -> bool:
        """Return True if a file name matches pytest default test file patterns."""
        file_name = path.name
        return file_name.startswith("test_") or file_name.endswith("_test.py")

    def _should_ignore_path(self, path: Path) -> bool:
        """Return True if the path is inside an ignored directory."""
        return any(part in self.ignored_directories for part in path.parts)

    def _safe_relative_path(self, path: Path, root: Path) -> Path:
        """Return a path relative to root when possible."""
        try:
            return path.relative_to(root)
        except ValueError:
            return path