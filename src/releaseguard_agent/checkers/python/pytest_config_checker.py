import configparser
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)


@dataclass(frozen=True)
class PytestConfigFile:
    """Detected pytest configuration file."""

    path: Path
    config_type: str
    pythonpath_entries: tuple[str, ...]


class PytestConfigChecker(BaseChecker):
    """Check pytest configuration and src layout import reproducibility."""

    name = "pytest_config_checker"
    description = "Checks pytest configuration and src layout import behavior."

    config_file_names: tuple[str, ...] = (
        "pytest.ini",
        ".pytest.ini",
        "pytest.toml",
        ".pytest.toml",
        "pyproject.toml",
        "tox.ini",
        "setup.cfg",
    )

    def run(self, project_path: Path) -> list[CheckResult]:
        """Run pytest configuration checks against a target project path."""
        config_files = self._find_pytest_config_files(project_path)

        return [
            self._build_config_exists_result(project_path, config_files),
            self._build_src_layout_result(project_path, config_files),
        ]

    def _build_config_exists_result(
        self,
        project_path: Path,
        config_files: list[PytestConfigFile],
    ) -> CheckResult:
        """Build result for RG-TEST-006."""
        found_configs = [
            self._safe_relative_path(config_file.path, project_path)
            for config_file in config_files
        ]

        if config_files:
            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="Pytest configuration found",
                message="The project has pytest configuration.",
                evidence=[
                    f"Found pytest config: {relative_path}"
                    for relative_path in found_configs
                ],
                recommendation=None,
                rule_id="RG-TEST-006",
                rule_source=(
                    "pytest Good Integration Practices; "
                    "ReleaseGuard default policy"
                ),
                file_path=str(project_path),
                metadata={
                    "checked_files": list(self.config_file_names),
                    "found_configs": found_configs,
                    "support_level": "releaseguard-default",
                    "blocking_policy": "warn",
                    "evidence_type": "file_exists",
                },
            )

        return CheckResult(
            checker_name=self.name,
            status=CheckStatus.WARNING,
            risk_level=RiskLevel.MEDIUM,
            title="Pytest configuration missing",
            message=(
                "The project does not have a recognized pytest configuration "
                "file in the project root."
            ),
            evidence=[
                "Checked pytest config files: "
                + ", ".join(self.config_file_names),
                "No pytest configuration file was found.",
            ],
            recommendation=(
                "Add pytest configuration, such as pytest.ini or "
                "pyproject.toml with [tool.pytest.ini_options], so test "
                "discovery and import behavior are reproducible."
            ),
            rule_id="RG-TEST-006",
            rule_source=(
                "pytest Good Integration Practices; "
                "ReleaseGuard default policy"
            ),
            file_path=str(project_path),
            metadata={
                "checked_files": list(self.config_file_names),
                "found_configs": [],
                "support_level": "releaseguard-default",
                "blocking_policy": "warn",
                "evidence_type": "file_exists",
            },
        )

    def _build_src_layout_result(
        self,
        project_path: Path,
        config_files: list[PytestConfigFile],
    ) -> CheckResult:
        """Build result for RG-TEST-007."""
        src_dir = project_path / "src"

        if not src_dir.is_dir():
            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.SKIPPED,
                risk_level=RiskLevel.INFO,
                title="src layout not detected",
                message="The project does not use a root src/ layout.",
                evidence=["Checked for directory: src/"],
                recommendation=None,
                rule_id="RG-TEST-007",
                rule_source="pytest Good Integration Practices",
                file_path=str(project_path),
                metadata={
                    "src_layout_detected": False,
                    "pythonpath_entries": [],
                    "accepted_config_files": [],
                    "support_level": "source-backed",
                    "blocking_policy": "conditional",
                    "evidence_type": "config_value",
                },
            )

        configs_with_src = [
            config_file
            for config_file in config_files
            if self._contains_src_pythonpath(config_file.pythonpath_entries)
        ]
        pythonpath_entries = [
            entry
            for config_file in config_files
            for entry in config_file.pythonpath_entries
        ]

        if configs_with_src:
            accepted_config_files = [
                self._safe_relative_path(config_file.path, project_path)
                for config_file in configs_with_src
            ]

            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="src layout import configuration found",
                message="The project configures pytest to import from src/.",
                evidence=[
                    "Detected src/ layout.",
                    "Found pytest pythonpath entry for src/.",
                    "Accepted config files: " + ", ".join(accepted_config_files),
                ],
                recommendation=None,
                rule_id="RG-TEST-007",
                rule_source="pytest Good Integration Practices",
                file_path=str(src_dir),
                metadata={
                    "src_layout_detected": True,
                    "pythonpath_entries": pythonpath_entries,
                    "accepted_config_files": accepted_config_files,
                    "support_level": "source-backed",
                    "blocking_policy": "conditional",
                    "evidence_type": "config_value",
                },
            )

        return CheckResult(
            checker_name=self.name,
            status=CheckStatus.WARNING,
            risk_level=RiskLevel.MEDIUM,
            title="src layout import configuration missing",
            message=(
                "The project uses a root src/ layout, but pytest configuration "
                "does not declare pythonpath = src."
            ),
            evidence=[
                "Detected src/ layout.",
                "No pytest pythonpath entry for src/ was found.",
            ],
            recommendation=(
                "Configure pytest import behavior, for example by adding "
                "pythonpath = src to pytest.ini, or by using a documented "
                "editable-install workflow such as python -m pip install -e ."
            ),
            rule_id="RG-TEST-007",
            rule_source="pytest Good Integration Practices",
            file_path=str(src_dir),
            metadata={
                "src_layout_detected": True,
                "pythonpath_entries": pythonpath_entries,
                "accepted_config_files": [],
                "support_level": "source-backed",
                "blocking_policy": "conditional",
                "evidence_type": "config_value",
            },
        )

    def _find_pytest_config_files(self, project_path: Path) -> list[PytestConfigFile]:
        """Return pytest configuration files found in the project root."""
        config_files: list[PytestConfigFile] = []

        for file_name in self.config_file_names:
            path = project_path / file_name
            if not path.is_file():
                continue

            config_type = self._detect_config_type(path)
            if config_type is None:
                continue

            config_files.append(
                PytestConfigFile(
                    path=path,
                    config_type=config_type,
                    pythonpath_entries=self._extract_pythonpath_entries(path),
                )
            )

        return config_files

    def _detect_config_type(self, path: Path) -> str | None:
        """Return the pytest config type if the file is a pytest config."""
        if path.name in {"pytest.ini", ".pytest.ini"}:
            return "pytest-ini"

        if path.name in {"pytest.toml", ".pytest.toml"}:
            return "pytest-toml"

        if path.name == "pyproject.toml":
            data = self._load_toml(path)
            tool_table = data.get("tool", {})
            pytest_table = tool_table.get("pytest", {})
            if isinstance(pytest_table, dict) and "ini_options" in pytest_table:
                return "pyproject-toml"
            return None

        if path.name in {"tox.ini", "setup.cfg"}:
            parser = self._load_ini(path)
            if parser is None:
                return None

            if parser.has_section("pytest") or parser.has_section("tool:pytest"):
                return path.name

        return None

    def _extract_pythonpath_entries(self, path: Path) -> tuple[str, ...]:
        """Extract pytest pythonpath entries from a config file."""
        if path.name in {"pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg"}:
            parser = self._load_ini(path)
            if parser is None:
                return ()

            for section_name in ("pytest", "tool:pytest"):
                if parser.has_option(section_name, "pythonpath"):
                    value = parser.get(section_name, "pythonpath")
                    return self._normalize_pythonpath_entries(value)

            return ()

        if path.name == "pyproject.toml":
            data = self._load_toml(path)
            value = (
                data.get("tool", {})
                .get("pytest", {})
                .get("ini_options", {})
                .get("pythonpath")
            )
            return self._normalize_pythonpath_entries(value)

        if path.name in {"pytest.toml", ".pytest.toml"}:
            data = self._load_toml(path)
            value = data.get("pytest", {}).get("pythonpath")
            return self._normalize_pythonpath_entries(value)

        return ()

    def _load_ini(self, path: Path) -> configparser.ConfigParser | None:
        """Load an INI-style pytest config file."""
        parser = configparser.ConfigParser(interpolation=None)

        try:
            parser.read(path, encoding="utf-8")
        except configparser.Error:
            return None

        return parser

    def _load_toml(self, path: Path) -> dict[str, Any]:
        """Load a TOML config file."""
        try:
            return tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            return {}

    def _normalize_pythonpath_entries(self, value: Any) -> tuple[str, ...]:
        """Normalize pytest pythonpath config values to strings."""
        if value is None:
            return ()

        if isinstance(value, str):
            normalized = value.replace(",", " ").replace(";", " ")
            return tuple(entry for entry in normalized.split() if entry)

        if isinstance(value, list | tuple):
            entries: list[str] = []
            for item in value:
                entries.extend(self._normalize_pythonpath_entries(item))
            return tuple(entries)

        return (str(value),)

    def _contains_src_pythonpath(self, entries: tuple[str, ...]) -> bool:
        """Return True if pythonpath entries include src."""
        for entry in entries:
            normalized = entry.strip().replace("\\", "/").rstrip("/")
            if normalized.startswith("./"):
                normalized = normalized[2:]

            if normalized == "src":
                return True

        return False

    def _safe_relative_path(self, path: Path, root: Path) -> str:
        """Return a string path relative to root when possible."""
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)