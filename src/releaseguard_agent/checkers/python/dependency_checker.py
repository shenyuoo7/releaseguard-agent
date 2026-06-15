from pathlib import Path

from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)


class DependencyChecker(BaseChecker):
    """Check whether a Python project declares its dependencies."""

    name = "dependency_checker"
    description = "Checks whether the project has dependency declaration files."

    dependency_files: tuple[str, ...] = (
        "requirements.txt",
        "pyproject.toml",
        "Pipfile",
        "poetry.lock",
        "uv.lock",
        "setup.py",
        "setup.cfg",
    )

    def run(self, project_path: Path) -> list[CheckResult]:
        """Run dependency declaration checks against a target project path."""
        found_files = self._find_dependency_files(project_path)

        if found_files:
            return [
                CheckResult(
                    checker_name=self.name,
                    status=CheckStatus.PASSED,
                    risk_level=RiskLevel.INFO,
                    title="Dependency declaration found",
                    message="The project declares Python dependencies.",
                    evidence=[
                        f"Found dependency file: {file_path.name}"
                        for file_path in found_files
                    ],
                    recommendation=None,
                    rule_id="RG-DEPS-001",
                    rule_source="The Twelve-Factor App - Dependencies",
                    file_path=str(project_path),
                    metadata={
                        "checked_files": list(self.dependency_files),
                        "found_files": [
                            file_path.name for file_path in found_files
                        ],
                        "support_level": "source-backed",
                        "blocking_policy": "block",
                        "evidence_type": "file_exists",
                    },
                )
            ]

        return [
            CheckResult(
                checker_name=self.name,
                status=CheckStatus.FAILED,
                risk_level=RiskLevel.HIGH,
                title="Dependency declaration missing",
                message=(
                    "The project does not have a recognized Python dependency "
                    "declaration file in the project root."
                ),
                evidence=[
                    "Checked dependency files: "
                    + ", ".join(self.dependency_files),
                    "No dependency declaration file was found in the project root.",
                ],
                recommendation=(
                    "Add a dependency declaration file such as requirements.txt "
                    "or pyproject.toml so the release environment can be "
                    "reproduced reliably."
                ),
                rule_id="RG-DEPS-001",
                rule_source="The Twelve-Factor App - Dependencies",
                file_path=str(project_path),
                metadata={
                    "checked_files": list(self.dependency_files),
                    "found_files": [],
                    "support_level": "source-backed",
                    "blocking_policy": "block",
                    "evidence_type": "file_exists",
                },
            )
        ]

    def _find_dependency_files(self, project_path: Path) -> list[Path]:
        """Return dependency declaration files found in the project root."""
        return [
            project_path / file_name
            for file_name in self.dependency_files
            if (project_path / file_name).is_file()
        ]