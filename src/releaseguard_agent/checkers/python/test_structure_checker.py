import configparser
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

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

    pytest_config_file_names: tuple[str, ...] = (
        "pytest.ini",
        ".pytest.ini",
        "pytest.toml",
        ".pytest.toml",
        "pyproject.toml",
        "tox.ini",
        "setup.cfg",
    )

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
        configured_testpaths = self._extract_configured_testpaths(project_path)
        search_roots = self._build_test_search_roots(
            project_path,
            configured_testpaths,
        )
        test_files = self._find_test_files(search_roots)
        relative_search_roots = [
            str(self._safe_relative_path(search_root, project_path))
            for search_root in search_roots
        ]

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
                    "configured_testpaths": list(configured_testpaths),
                    "search_roots": relative_search_roots,
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
                "configured_testpaths": list(configured_testpaths),
                "search_roots": relative_search_roots,
                "found_file_count": 0,
                "found_files": [],
                "support_level": "source-backed",
                "blocking_policy": "block",
                "evidence_type": "file_glob_matches",
            },
        )

    def _find_test_files(self, search_roots: tuple[Path, ...]) -> list[Path]:
        """Return pytest-discoverable test files found under search roots."""
        test_files: list[Path] = []

        for search_root in search_roots:
            if not search_root.exists() or self._should_ignore_path(search_root):
                continue

            candidates: Iterable[Path]
            if search_root.is_file():
                candidates = [search_root]
            else:
                candidates = search_root.rglob("*.py")

            for python_file in candidates:
                if (
                    python_file.is_file()
                    and not self._should_ignore_path(python_file)
                    and self._is_pytest_test_file(python_file)
                ):
                    test_files.append(python_file)

        return sorted(set(test_files), key=lambda path: str(path))

    def _is_pytest_test_file(self, path: Path) -> bool:
        """Return True if a file name matches pytest default test file patterns."""
        file_name = path.name
        return file_name.startswith("test_") or file_name.endswith("_test.py")

    def _build_test_search_roots(
        self,
        project_path: Path,
        configured_testpaths: tuple[str, ...],
    ) -> tuple[Path, ...]:
        """Return directories or files that should be scanned for tests."""
        if not configured_testpaths:
            return (project_path,)

        return tuple(project_path / entry for entry in configured_testpaths)

    def _extract_configured_testpaths(self, project_path: Path) -> tuple[str, ...]:
        """Return pytest testpaths configured in the project root."""
        for file_name in self.pytest_config_file_names:
            path = project_path / file_name
            if not path.is_file():
                continue

            testpaths = self._extract_testpaths_from_config(path)
            if testpaths:
                return testpaths

        return ()

    def _extract_testpaths_from_config(self, path: Path) -> tuple[str, ...]:
        """Extract pytest testpaths entries from a supported config file."""
        if path.name in {"pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg"}:
            parser = self._load_ini(path)
            if parser is None:
                return ()

            for section_name in ("pytest", "tool:pytest"):
                if parser.has_option(section_name, "testpaths"):
                    value = parser.get(section_name, "testpaths")
                    return self._normalize_path_entries(value)

            return ()

        if path.name == "pyproject.toml":
            data = self._load_toml(path)
            value = (
                data.get("tool", {})
                .get("pytest", {})
                .get("ini_options", {})
                .get("testpaths")
            )
            return self._normalize_path_entries(value)

        if path.name in {"pytest.toml", ".pytest.toml"}:
            data = self._load_toml(path)
            value = data.get("pytest", {}).get("testpaths")
            return self._normalize_path_entries(value)

        return ()

    def _load_ini(self, path: Path) -> configparser.ConfigParser | None:
        """Load an INI-style pytest config file."""
        parser = configparser.ConfigParser(interpolation=None)

        try:
            parser.read(path, encoding="utf-8")
        except (configparser.Error, OSError, UnicodeDecodeError):
            return None

        return parser

    def _load_toml(self, path: Path) -> dict[str, Any]:
        """Load a TOML config file."""
        try:
            return tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
            return {}

    def _normalize_path_entries(self, value: Any) -> tuple[str, ...]:
        """Normalize pytest path config values to strings."""
        if value is None:
            return ()

        if isinstance(value, str):
            normalized = value.replace(",", " ").replace(";", " ")
            return tuple(entry for entry in normalized.split() if entry)

        if isinstance(value, list | tuple):
            entries: list[str] = []
            for item in value:
                entries.extend(self._normalize_path_entries(item))
            return tuple(entries)

        return (str(value),)

    def _should_ignore_path(self, path: Path) -> bool:
        """Return True if the path is inside an ignored directory."""
        return any(part in self.ignored_directories for part in path.parts)

    def _safe_relative_path(self, path: Path, root: Path) -> Path:
        """Return a path relative to root when possible."""
        try:
            return path.relative_to(root)
        except ValueError:
            return path
