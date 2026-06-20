from pathlib import Path

from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.scanners.dockerfile_scanner import (
    DockerInstruction,
    DockerfileScan,
    DockerfileScanner,
)


class DockerStyleChecker(BaseChecker):
    """Check Dockerfile instruction keyword casing."""

    name = "docker_style_checker"
    description = (
        "Checks whether Dockerfile instruction keywords use "
        "the uppercase convention."
    )

    rule_id = "RG-DOCKER-008"
    rule_source = "Dockerfile reference"

    def __init__(
        self,
        scanner: DockerfileScanner | None = None,
    ):
        self.scanner = scanner or DockerfileScanner()

    def run(self, project_path: Path) -> list[CheckResult]:
        """Run Dockerfile instruction style checks."""

        project_path = Path(project_path)
        scan = self.scanner.scan(project_path)

        if not scan.exists:
            return [
                self._make_result(
                    status=CheckStatus.SKIPPED,
                    risk_level=RiskLevel.INFO,
                    title="Dockerfile style check not applicable",
                    message=(
                        "No root Dockerfile was found, so "
                        "instruction casing was not checked."
                    ),
                    evidence=[
                        "Checked for root file: Dockerfile",
                    ],
                    recommendation=None,
                    file_path=str(project_path),
                    scan=scan,
                    mismatches=[],
                )
            ]

        dockerfile_path = project_path / scan.file_path

        if scan.read_error is not None:
            return [
                self._make_result(
                    status=CheckStatus.SKIPPED,
                    risk_level=RiskLevel.INFO,
                    title="Dockerfile style check skipped",
                    message=(
                        "The Dockerfile could not be read, so "
                        "instruction casing was not checked."
                    ),
                    evidence=[
                        f"Read error: {scan.read_error}",
                    ],
                    recommendation=None,
                    file_path=str(dockerfile_path),
                    scan=scan,
                    mismatches=[],
                )
            ]

        if not scan.instructions:
            evidence = [
                "No parseable Docker instructions were found.",
            ]
            evidence.extend(
                (
                    "Dockerfile parse issue at line "
                    f"{issue.line_number}: {issue.message}"
                )
                for issue in scan.issues
            )

            return [
                self._make_result(
                    status=CheckStatus.SKIPPED,
                    risk_level=RiskLevel.INFO,
                    title="Dockerfile style check skipped",
                    message=(
                        "No parseable Docker instructions were "
                        "available for casing review."
                    ),
                    evidence=evidence,
                    recommendation=None,
                    file_path=str(dockerfile_path),
                    scan=scan,
                    mismatches=[],
                )
            ]

        mismatches = [
            instruction
            for instruction in scan.instructions
            if instruction.original_keyword
            != instruction.keyword
        ]

        if mismatches:
            return [
                self._make_result(
                    status=CheckStatus.WARNING,
                    risk_level=RiskLevel.LOW,
                    title=(
                        "Dockerfile instruction casing "
                        "is inconsistent"
                    ),
                    message=(
                        "One or more Dockerfile instruction "
                        "keywords do not use the documented "
                        "uppercase convention."
                    ),
                    evidence=[
                        self._style_evidence(instruction)
                        for instruction in mismatches
                    ],
                    recommendation=(
                        "Use uppercase Dockerfile instruction "
                        "keywords for conventional readability. "
                        "This style recommendation does not affect "
                        "Dockerfile validity."
                    ),
                    file_path=str(dockerfile_path),
                    scan=scan,
                    mismatches=mismatches,
                )
            ]

        return [
            self._make_result(
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title=(
                    "Dockerfile instruction casing "
                    "follows convention"
                ),
                message=(
                    "All parsed Dockerfile instruction keywords "
                    "use the uppercase convention."
                ),
                evidence=[
                    (
                        f"Checked {len(scan.instructions)} "
                        "Dockerfile instruction(s); all keywords "
                        "are uppercase."
                    ),
                ],
                recommendation=None,
                file_path=str(dockerfile_path),
                scan=scan,
                mismatches=[],
            )
        ]

    def _style_evidence(
        self,
        instruction: DockerInstruction,
    ) -> str:
        if instruction.start_line == instruction.end_line:
            location = str(instruction.start_line)
        else:
            location = (
                f"{instruction.start_line}-"
                f"{instruction.end_line}"
            )

        return (
            "Non-uppercase Docker instruction at "
            f"Dockerfile:{location}: found "
            f"`{instruction.original_keyword}`; expected "
            f"`{instruction.keyword}`."
        )

    def _make_result(
        self,
        *,
        status: CheckStatus,
        risk_level: RiskLevel,
        title: str,
        message: str,
        evidence: list[str],
        recommendation: str | None,
        file_path: str,
        scan: DockerfileScan,
        mismatches: list[DockerInstruction],
    ) -> CheckResult:
        return CheckResult(
            checker_name=self.name,
            status=status,
            risk_level=risk_level,
            title=title,
            message=message,
            evidence=evidence,
            recommendation=recommendation,
            rule_id=self.rule_id,
            rule_source=self.rule_source,
            file_path=file_path,
            metadata={
                "support_level": "source-backed",
                "blocking_policy": "info",
                "evidence_type": (
                    "docker_instruction_style"
                ),
                "dockerfile_exists": scan.exists,
                "readable": scan.readable,
                "checked_instruction_count": len(
                    scan.instructions
                ),
                "mismatched_instruction_count": len(
                    mismatches
                ),
                "mismatched_instructions": [
                    instruction.to_dict()
                    for instruction in mismatches
                ],
                "parser_directive_count": len(
                    scan.parser_directives
                ),
                "parse_issues": [
                    issue.to_dict()
                    for issue in scan.issues
                ],
                "read_error": scan.read_error,
            },
        )
