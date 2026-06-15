from pathlib import Path

from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)


class EnvExampleChecker(BaseChecker):
    """Check whether a project provides an environment variable example file."""

    name = "env_example_checker"
    description = "Checks whether the project has a .env.example file."

    env_example_file = ".env.example"

    env_usage_patterns: tuple[str, ...] = (
        "os.environ",
        "os.getenv",
        "getenv(",
        "environ.get",
        "BaseSettings",
        "pydantic_settings",
        "load_dotenv",
    )

    ignored_directories: tuple[str, ...] = (
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "outputs",
    )

    def run(self, project_path: Path) -> list[CheckResult]:
        """Run .env.example checks against a target project path."""
        env_example_path = project_path / self.env_example_file

        if env_example_path.is_file():
            return [
                CheckResult(
                    checker_name=self.name,
                    status=CheckStatus.PASSED,
                    risk_level=RiskLevel.INFO,
                    title=".env.example file found",
                    message="The project provides an environment variable example file.",
                    evidence=[
                        f"Found environment example file: {self.env_example_file}"
                    ],
                    recommendation=None,
                    rule_id="RG-CONFIG-001",
                    rule_source=(
                        "The Twelve-Factor App - Config; "
                        "ReleaseGuard default policy"
                    ),
                    file_path=str(env_example_path),
                    metadata={
                        "checked_file": self.env_example_file,
                        "found_file": self.env_example_file,
                        "env_usage_detected": None,
                        "support_level": "releaseguard-default",
                        "blocking_policy": "conditional",
                        "evidence_type": "file_exists",
                    },
                )
            ]

        env_usage_evidence = self._find_environment_usage(project_path)

        if env_usage_evidence:
            return [
                CheckResult(
                    checker_name=self.name,
                    status=CheckStatus.FAILED,
                    risk_level=RiskLevel.HIGH,
                    title=".env.example file missing",
                    message=(
                        "The project appears to use environment-based "
                        "configuration, but no .env.example file was found "
                        "in the project root."
                    ),
                    evidence=[
                        f"Checked for file: {self.env_example_file}",
                        "No .env.example file was found in the project root.",
                        *env_usage_evidence,
                    ],
                    recommendation=(
                        "Add a .env.example file that lists required "
                        "environment variables with safe example values. "
                        "Do not include real secrets."
                    ),
                    rule_id="RG-CONFIG-001",
                    rule_source=(
                        "The Twelve-Factor App - Config; "
                        "ReleaseGuard default policy"
                    ),
                    file_path=str(project_path),
                    metadata={
                        "checked_file": self.env_example_file,
                        "found_file": None,
                        "env_usage_detected": True,
                        "support_level": "releaseguard-default",
                        "blocking_policy": "conditional",
                        "evidence_type": "file_exists",
                        "secondary_evidence_type": "matched_lines",
                    },
                )
            ]

        return [
            CheckResult(
                checker_name=self.name,
                status=CheckStatus.WARNING,
                risk_level=RiskLevel.MEDIUM,
                title=".env.example file not found",
                message=(
                    "No .env.example file was found. The lightweight scan did "
                    "not detect obvious environment variable usage, so this is "
                    "reported as a warning for now."
                ),
                evidence=[
                    f"Checked for file: {self.env_example_file}",
                    "No .env.example file was found in the project root.",
                    "No obvious environment variable usage was detected.",
                ],
                recommendation=(
                    "If the project needs environment variables in any runtime "
                    "environment, add a .env.example file with safe example values."
                ),
                rule_id="RG-CONFIG-001",
                rule_source=(
                    "The Twelve-Factor App - Config; "
                    "ReleaseGuard default policy"
                ),
                file_path=str(project_path),
                metadata={
                    "checked_file": self.env_example_file,
                    "found_file": None,
                    "env_usage_detected": False,
                    "support_level": "releaseguard-default",
                    "blocking_policy": "conditional",
                    "evidence_type": "file_exists",
                },
            )
        ]

    def _find_environment_usage(self, project_path: Path) -> list[str]:
        """Return lightweight evidence of environment variable usage."""
        matches: list[str] = []

        for python_file in project_path.rglob("*.py"):
            if self._should_ignore_path(python_file):
                continue

            try:
                lines = python_file.read_text(
                    encoding="utf-8",
                    errors="ignore",
                ).splitlines()
            except OSError:
                continue

            for line_number, line in enumerate(lines, start=1):
                matched_pattern = self._match_env_usage_pattern(line)
                if matched_pattern is None:
                    continue

                relative_path = self._safe_relative_path(
                    python_file,
                    project_path,
                )
                matches.append(
                    "Found environment config usage: "
                    f"{relative_path}:{line_number} "
                    f"matched pattern `{matched_pattern}`"
                )

                if len(matches) >= 5:
                    return matches

        return matches

    def _match_env_usage_pattern(self, line: str) -> str | None:
        """Return the first environment usage pattern found in a line."""
        for pattern in self.env_usage_patterns:
            if pattern in line:
                return pattern

        return None

    def _should_ignore_path(self, path: Path) -> bool:
        """Return True if the path is inside an ignored directory."""
        return any(part in self.ignored_directories for part in path.parts)

    def _safe_relative_path(self, path: Path, root: Path) -> Path:
        """Return a path relative to root when possible."""
        try:
            return path.relative_to(root)
        except ValueError:
            return path