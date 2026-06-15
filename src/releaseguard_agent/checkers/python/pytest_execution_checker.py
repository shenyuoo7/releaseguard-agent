import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)


@dataclass(frozen=True)
class PytestCommandResult:
    """Result of running a pytest command."""

    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False


class PytestExecutionChecker(BaseChecker):
    """Check whether pytest can collect and run tests."""

    name = "pytest_execution_checker"
    description = "Runs pytest collection and execution checks."

    timeout_seconds = 30

    def run(self, project_path: Path) -> list[CheckResult]:
        """Run pytest execution checks against a target project path."""
        collect_result = self._run_pytest(
            project_path=project_path,
            pytest_args=("--collect-only", "-q"),
        )
        run_result = self._run_pytest(
            project_path=project_path,
            pytest_args=("-q",),
        )

        return [
            self._build_collected_tests_result(project_path, collect_result),
            self._build_collect_command_result(project_path, collect_result),
            self._build_run_command_result(project_path, run_result),
        ]

    def _build_collected_tests_result(
        self,
        project_path: Path,
        command_result: PytestCommandResult,
    ) -> CheckResult:
        """Build result for RG-TEST-003."""
        collected_count = self._parse_collected_test_count(command_result)

        if command_result.exit_code == 0 and collected_count and collected_count > 0:
            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="Pytest collected tests",
                message="pytest collected at least one test.",
                evidence=[
                    f"Command: {command_result.command}",
                    f"Collected tests: {collected_count}",
                    *self._output_evidence(command_result),
                ],
                recommendation=None,
                rule_id="RG-TEST-003",
                rule_source="pytest Good Integration Practices",
                file_path=str(project_path),
                metadata={
                    "command": command_result.command,
                    "exit_code": command_result.exit_code,
                    "collected_test_count": collected_count,
                    "duration_seconds": command_result.duration_seconds,
                    "support_level": "source-backed",
                    "blocking_policy": "block",
                    "evidence_type": "collected_tests",
                },
            )

        return CheckResult(
            checker_name=self.name,
            status=CheckStatus.FAILED,
            risk_level=RiskLevel.HIGH,
            title="No pytest-collectable tests found",
            message="pytest did not collect any tests successfully.",
            evidence=[
                f"Command: {command_result.command}",
                f"Exit code: {command_result.exit_code}",
                f"Collected tests: {collected_count or 0}",
                *self._output_evidence(command_result),
            ],
            recommendation=(
                "Add pytest-discoverable test functions or methods, then make "
                "sure pytest can collect them successfully."
            ),
            rule_id="RG-TEST-003",
            rule_source="pytest Good Integration Practices",
            file_path=str(project_path),
            metadata={
                "command": command_result.command,
                "exit_code": command_result.exit_code,
                "collected_test_count": collected_count or 0,
                "duration_seconds": command_result.duration_seconds,
                "support_level": "source-backed",
                "blocking_policy": "block",
                "evidence_type": "collected_tests",
            },
        )

    def _build_collect_command_result(
        self,
        project_path: Path,
        command_result: PytestCommandResult,
    ) -> CheckResult:
        """Build result for RG-TEST-004."""
        if command_result.exit_code == 0:
            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="Pytest collection command succeeded",
                message="python -m pytest --collect-only -q completed successfully.",
                evidence=[
                    f"Command: {command_result.command}",
                    "Exit code: 0",
                    *self._output_evidence(command_result),
                ],
                recommendation=None,
                rule_id="RG-TEST-004",
                rule_source="pytest Good Integration Practices",
                file_path=str(project_path),
                metadata={
                    "command": command_result.command,
                    "exit_code": command_result.exit_code,
                    "duration_seconds": command_result.duration_seconds,
                    "timed_out": command_result.timed_out,
                    "support_level": "source-backed",
                    "blocking_policy": "block",
                    "evidence_type": "command_result",
                },
            )

        return CheckResult(
            checker_name=self.name,
            status=CheckStatus.FAILED,
            risk_level=RiskLevel.HIGH,
            title="Pytest collection command failed",
            message="python -m pytest --collect-only -q did not complete successfully.",
            evidence=[
                f"Command: {command_result.command}",
                f"Exit code: {command_result.exit_code}",
                *self._output_evidence(command_result),
            ],
            recommendation=(
                "Fix pytest collection errors, import errors, or test discovery "
                "configuration before release."
            ),
            rule_id="RG-TEST-004",
            rule_source="pytest Good Integration Practices",
            file_path=str(project_path),
            metadata={
                "command": command_result.command,
                "exit_code": command_result.exit_code,
                "duration_seconds": command_result.duration_seconds,
                "timed_out": command_result.timed_out,
                "support_level": "source-backed",
                "blocking_policy": "block",
                "evidence_type": "command_result",
            },
        )

    def _build_run_command_result(
        self,
        project_path: Path,
        command_result: PytestCommandResult,
    ) -> CheckResult:
        """Build result for RG-TEST-005."""
        if command_result.exit_code == 0:
            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="Pytest run succeeded",
                message="python -m pytest -q completed successfully.",
                evidence=[
                    f"Command: {command_result.command}",
                    "Exit code: 0",
                    *self._output_evidence(command_result),
                ],
                recommendation=None,
                rule_id="RG-TEST-005",
                rule_source="pytest Good Integration Practices",
                file_path=str(project_path),
                metadata={
                    "command": command_result.command,
                    "exit_code": command_result.exit_code,
                    "duration_seconds": command_result.duration_seconds,
                    "timed_out": command_result.timed_out,
                    "support_level": "source-backed",
                    "blocking_policy": "block",
                    "evidence_type": "command_result",
                },
            )

        return CheckResult(
            checker_name=self.name,
            status=CheckStatus.FAILED,
            risk_level=RiskLevel.CRITICAL,
            title="Pytest run failed",
            message="python -m pytest -q did not complete successfully.",
            evidence=[
                f"Command: {command_result.command}",
                f"Exit code: {command_result.exit_code}",
                *self._output_evidence(command_result),
            ],
            recommendation=(
                "Fix failing tests before release. A project should not be "
                "released while its automated test suite is failing."
            ),
            rule_id="RG-TEST-005",
            rule_source="pytest Good Integration Practices",
            file_path=str(project_path),
            metadata={
                "command": command_result.command,
                "exit_code": command_result.exit_code,
                "duration_seconds": command_result.duration_seconds,
                "timed_out": command_result.timed_out,
                "support_level": "source-backed",
                "blocking_policy": "block",
                "evidence_type": "command_result",
            },
        )

    def _run_pytest(
        self,
        project_path: Path,
        pytest_args: tuple[str, ...],
    ) -> PytestCommandResult:
        """Run a pytest command in the target project directory."""
        command = [sys.executable, "-m", "pytest", *pytest_args]
        command_display = "python -m pytest " + " ".join(pytest_args)
        start_time = time.perf_counter()

        try:
            completed = subprocess.run(
                command,
                cwd=project_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
            duration_seconds = round(time.perf_counter() - start_time, 4)

            return PytestCommandResult(
                command=command_display,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_seconds=duration_seconds,
            )
        except subprocess.TimeoutExpired as error:
            duration_seconds = round(time.perf_counter() - start_time, 4)

            return PytestCommandResult(
                command=command_display,
                exit_code=None,
                stdout=self._coerce_output(error.stdout),
                stderr=self._coerce_output(error.stderr),
                duration_seconds=duration_seconds,
                timed_out=True,
            )
        except OSError as error:
            duration_seconds = round(time.perf_counter() - start_time, 4)

            return PytestCommandResult(
                command=command_display,
                exit_code=None,
                stdout="",
                stderr=str(error),
                duration_seconds=duration_seconds,
            )

    def _parse_collected_test_count(
        self,
        command_result: PytestCommandResult,
    ) -> int | None:
        """Parse pytest collected test count from command output."""
        output = f"{command_result.stdout}\n{command_result.stderr}"
        match = re.search(r"(\d+)\s+(?:test|tests)\s+collected", output)

        if match is None:
            return None

        return int(match.group(1))

    def _output_evidence(self, command_result: PytestCommandResult) -> list[str]:
        """Return compact command output evidence."""
        output = f"{command_result.stdout}\n{command_result.stderr}"
        lines = [line.strip() for line in output.splitlines() if line.strip()]

        if command_result.timed_out:
            lines.append(f"Command timed out after {self.timeout_seconds} seconds.")

        if not lines:
            return ["Command produced no output."]

        output_summary = " | ".join(lines[-8:])
        if len(output_summary) > 1000:
            output_summary = output_summary[:1000] + "..."

        return [f"Output summary: {output_summary}"]

    def _coerce_output(self, output: str | bytes | None) -> str:
        """Convert subprocess timeout output to text."""
        if output is None:
            return ""

        if isinstance(output, bytes):
            return output.decode("utf-8", errors="replace")

        return output