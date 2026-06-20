import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

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


@dataclass(frozen=True)
class RootDockerfileIntent:
    """Compose evidence that expects the root Dockerfile."""

    compose_file: str
    service_name: str
    build_context: str
    dockerfile: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "compose_file": self.compose_file,
            "service_name": self.service_name,
            "build_context": self.build_context,
            "dockerfile": self.dockerfile,
        }

    def to_evidence(self) -> str:
        dockerfile = self.dockerfile or "Dockerfile"

        return (
            "Compose root-build intent: "
            f"{self.compose_file} service `{self.service_name}` "
            f"uses context `{self.build_context}` and "
            f"Dockerfile `{dockerfile}`."
        )


class DockerChecker(BaseChecker):
    """Check root Dockerfile release readiness."""

    name = "docker_checker"
    description = (
        "Checks Dockerfile existence, structure, and runnable-image "
        "release signals."
    )

    compose_file_names: tuple[str, ...] = (
        "compose.yaml",
        "compose.yml",
        "docker-compose.yaml",
        "docker-compose.yml",
    )

    dependent_rule_ids: tuple[str, ...] = (
        "RG-DOCKER-002",
        "RG-DOCKER-003",
        "RG-DOCKER-004",
        "RG-DOCKER-005",
        "RG-DOCKER-006",
        "RG-DOCKER-007",
    )

    rule_names: dict[str, str] = {
        "RG-DOCKER-001": "Dockerfile existence",
        "RG-DOCKER-002": "FROM instruction",
        "RG-DOCKER-003": "FROM instruction position",
        "RG-DOCKER-004": "WORKDIR instruction",
        "RG-DOCKER-005": "COPY or ADD instruction",
        "RG-DOCKER-006": "Python dependency installation",
        "RG-DOCKER-007": "Container startup instruction",
    }

    rule_sources: dict[str, str] = {
        "RG-DOCKER-001": (
            "Dockerfile reference; ReleaseGuard container policy"
        ),
        "RG-DOCKER-002": "Dockerfile reference",
        "RG-DOCKER-003": "Dockerfile reference",
        "RG-DOCKER-004": (
            "Dockerfile reference; ReleaseGuard default policy"
        ),
        "RG-DOCKER-005": (
            "Dockerfile reference; ReleaseGuard default policy"
        ),
        "RG-DOCKER-006": (
            "Dockerfile reference; "
            "ReleaseGuard Python image policy"
        ),
        "RG-DOCKER-007": (
            "Dockerfile reference; "
            "ReleaseGuard runnable image policy"
        ),
    }

    rule_metadata: dict[str, dict[str, str]] = {
        "RG-DOCKER-001": {
            "support_level": "releaseguard-default",
            "blocking_policy": "conditional",
            "evidence_type": "file_exists",
        },
        "RG-DOCKER-002": {
            "support_level": "source-backed",
            "blocking_policy": "block",
            "evidence_type": "docker_instruction",
        },
        "RG-DOCKER-003": {
            "support_level": "source-backed",
            "blocking_policy": "block",
            "evidence_type": "docker_instruction_order",
        },
        "RG-DOCKER-004": {
            "support_level": "releaseguard-default",
            "blocking_policy": "warn",
            "evidence_type": "docker_instruction",
        },
        "RG-DOCKER-005": {
            "support_level": "releaseguard-default",
            "blocking_policy": "warn",
            "evidence_type": "docker_instruction",
        },
        "RG-DOCKER-006": {
            "support_level": "releaseguard-default",
            "blocking_policy": "warn",
            "evidence_type": "docker_instruction",
        },
        "RG-DOCKER-007": {
            "support_level": "releaseguard-default",
            "blocking_policy": "conditional",
            "evidence_type": "docker_instruction",
        },
    }

    dependency_install_patterns: tuple[
        re.Pattern[str],
        ...,
    ] = (
        re.compile(
            r"\b(?:python(?:3(?:\.\d+)?)?|py)"
            r"\s+-m\s+pip\s+install\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bpip(?:3(?:\.\d+)?)?\s+install\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\buv\s+(?:sync|pip\s+install)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bpoetry\s+install\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bpipenv\s+install\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bpdm\s+(?:install|sync)\b",
            re.IGNORECASE,
        ),
    )

    def __init__(
        self,
        scanner: DockerfileScanner | None = None,
    ):
        self.scanner = scanner or DockerfileScanner()

    def run(self, project_path: Path) -> list[CheckResult]:
        """Run Docker release checks against a project."""

        project_path = Path(project_path)
        scan = self.scanner.scan(project_path)

        intents, compose_diagnostics = (
            self._find_root_dockerfile_intents(project_path)
        )

        if not scan.exists:
            return self._missing_dockerfile_results(
                project_path,
                intents,
                compose_diagnostics,
            )

        dockerfile_path = project_path / scan.file_path

        results = [
            self._make_result(
                rule_id="RG-DOCKER-001",
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="Dockerfile found",
                message=(
                    "The project provides a root-level Dockerfile."
                ),
                evidence=[
                    f"Found Dockerfile: {scan.file_path}",
                ],
                recommendation=None,
                file_path=str(dockerfile_path),
                metadata={
                    "dockerfile_exists": True,
                    "container_intent": [
                        intent.to_dict() for intent in intents
                    ],
                    "compose_diagnostics": compose_diagnostics,
                    "parse_issues": [
                        issue.to_dict() for issue in scan.issues
                    ],
                },
            )
        ]

        if scan.read_error is not None:
            results.append(
                self._make_result(
                    rule_id="RG-DOCKER-002",
                    status=CheckStatus.FAILED,
                    risk_level=RiskLevel.HIGH,
                    title="Dockerfile could not be read",
                    message=(
                        "The Dockerfile exists, but ReleaseGuard "
                        "could not read it to verify the required "
                        "FROM instruction."
                    ),
                    evidence=[
                        f"Read error: {scan.read_error}",
                    ],
                    recommendation=(
                        "Make the Dockerfile readable and ensure it "
                        "uses UTF-8 compatible text encoding."
                    ),
                    file_path=str(dockerfile_path),
                    metadata={
                        "dockerfile_exists": True,
                        "readable": False,
                        "read_error": scan.read_error,
                    },
                )
            )

            results.extend(
                self._skipped_dependent_results(
                    project_path,
                    reason=(
                        "The Dockerfile could not be read, so this "
                        "structure check could not run."
                    ),
                    evidence=[
                        f"Read error: {scan.read_error}",
                    ],
                    rule_ids=(
                        "RG-DOCKER-003",
                        "RG-DOCKER-004",
                        "RG-DOCKER-005",
                        "RG-DOCKER-006",
                        "RG-DOCKER-007",
                    ),
                )
            )
            return results

        from_result, valid_from = self._check_from(
            project_path,
            scan,
        )
        results.append(from_result)

        results.append(
            self._check_from_position(
                project_path,
                scan,
                valid_from,
            )
        )
        results.append(
            self._check_instruction_presence(
                project_path=project_path,
                scan=scan,
                rule_id="RG-DOCKER-004",
                keywords=("WORKDIR",),
                passed_title="WORKDIR instruction found",
                passed_message=(
                    "The Dockerfile declares a working directory."
                ),
                warning_title="WORKDIR instruction missing",
                warning_message=(
                    "The Dockerfile does not declare a WORKDIR. "
                    "Relative paths may therefore depend on the base "
                    "image's current working directory."
                ),
                recommendation=(
                    "Add a WORKDIR instruction before copying files "
                    "or running application commands."
                ),
            )
        )
        results.append(
            self._check_instruction_presence(
                project_path=project_path,
                scan=scan,
                rule_id="RG-DOCKER-005",
                keywords=("COPY", "ADD"),
                passed_title="Application copy instruction found",
                passed_message=(
                    "The Dockerfile contains COPY or ADD."
                ),
                warning_title="COPY or ADD instruction missing",
                warning_message=(
                    "The Dockerfile does not contain COPY or ADD. "
                    "This may be valid for a specialized base image, "
                    "but ordinary application images usually need "
                    "build content."
                ),
                recommendation=(
                    "If the image packages project code or dependency "
                    "files, add an appropriate COPY instruction."
                ),
            )
        )
        results.append(
            self._check_dependency_installation(
                project_path,
                scan,
            )
        )
        results.append(
            self._check_instruction_presence(
                project_path=project_path,
                scan=scan,
                rule_id="RG-DOCKER-007",
                keywords=("CMD", "ENTRYPOINT"),
                passed_title="Container startup instruction found",
                passed_message=(
                    "The Dockerfile declares CMD or ENTRYPOINT."
                ),
                warning_title="Container startup instruction missing",
                warning_message=(
                    "The Dockerfile does not declare CMD or "
                    "ENTRYPOINT. A runnable service image normally "
                    "needs a default startup command."
                ),
                recommendation=(
                    "Add CMD or ENTRYPOINT when this image is expected "
                    "to start an application or service directly."
                ),
            )
        )

        return results

    def _missing_dockerfile_results(
        self,
        project_path: Path,
        intents: list[RootDockerfileIntent],
        compose_diagnostics: list[str],
    ) -> list[CheckResult]:
        intent_evidence = [
            intent.to_evidence() for intent in intents
        ]

        if intents:
            existence_result = self._make_result(
                rule_id="RG-DOCKER-001",
                status=CheckStatus.FAILED,
                risk_level=RiskLevel.HIGH,
                title="Expected Dockerfile is missing",
                message=(
                    "Docker Compose contains high-confidence root "
                    "build configuration, but no root Dockerfile was "
                    "found."
                ),
                evidence=[
                    "Checked for root file: Dockerfile",
                    *intent_evidence,
                ],
                recommendation=(
                    "Add the root Dockerfile expected by the Compose "
                    "build, or configure Compose to use the correct "
                    "alternate Dockerfile or inline Dockerfile."
                ),
                file_path=str(project_path),
                metadata={
                    "dockerfile_exists": False,
                    "container_intent_detected": True,
                    "container_intent": [
                        intent.to_dict() for intent in intents
                    ],
                    "compose_diagnostics": compose_diagnostics,
                },
            )
            reason = (
                "The expected root Dockerfile is missing, so this "
                "Dockerfile structure check could not run."
            )
        else:
            evidence = [
                "Checked for root file: Dockerfile",
                (
                    "No high-confidence Compose root-build intent "
                    "was detected."
                ),
            ]

            if compose_diagnostics:
                evidence.extend(compose_diagnostics)

            existence_result = self._make_result(
                rule_id="RG-DOCKER-001",
                status=CheckStatus.SKIPPED,
                risk_level=RiskLevel.INFO,
                title="Dockerfile check not applicable",
                message=(
                    "No root Dockerfile was found and ReleaseGuard "
                    "did not detect high-confidence evidence that this "
                    "project expects a root Dockerfile."
                ),
                evidence=evidence,
                recommendation=None,
                file_path=str(project_path),
                metadata={
                    "dockerfile_exists": False,
                    "container_intent_detected": False,
                    "container_intent": [],
                    "compose_diagnostics": compose_diagnostics,
                },
            )
            reason = (
                "No root Dockerfile or high-confidence root-build "
                "intent was detected."
            )

        return [
            existence_result,
            *self._skipped_dependent_results(
                project_path,
                reason=reason,
                evidence=[
                    "Dockerfile structure evidence is unavailable."
                ],
                rule_ids=self.dependent_rule_ids,
            ),
        ]

    def _check_from(
        self,
        project_path: Path,
        scan: DockerfileScan,
    ) -> tuple[CheckResult, bool]:
        dockerfile_path = project_path / scan.file_path
        from_instructions = scan.find_instructions("FROM")

        if not from_instructions:
            return (
                self._make_result(
                    rule_id="RG-DOCKER-002",
                    status=CheckStatus.FAILED,
                    risk_level=RiskLevel.HIGH,
                    title="FROM instruction missing",
                    message=(
                        "The Dockerfile does not contain the required "
                        "FROM instruction."
                    ),
                    evidence=[
                        "No FROM instruction was found.",
                        *self._issue_evidence(scan),
                    ],
                    recommendation=(
                        "Add a FROM instruction that selects the base "
                        "image for the build stage."
                    ),
                    file_path=str(dockerfile_path),
                    metadata={
                        "found_instructions": [],
                        "parse_issues": [
                            issue.to_dict()
                            for issue in scan.issues
                        ],
                    },
                ),
                False,
            )

        empty_from = [
            instruction
            for instruction in from_instructions
            if not instruction.arguments.strip()
        ]

        if empty_from:
            return (
                self._make_result(
                    rule_id="RG-DOCKER-002",
                    status=CheckStatus.FAILED,
                    risk_level=RiskLevel.HIGH,
                    title="FROM instruction is incomplete",
                    message=(
                        "At least one FROM instruction does not "
                        "declare a base image."
                    ),
                    evidence=[
                        self._instruction_evidence(instruction)
                        for instruction in empty_from
                    ],
                    recommendation=(
                        "Provide a valid base image after every FROM "
                        "instruction."
                    ),
                    file_path=str(dockerfile_path),
                    metadata={
                        "found_instructions": [
                            instruction.to_dict()
                            for instruction in from_instructions
                        ],
                        "empty_from_instructions": [
                            instruction.to_dict()
                            for instruction in empty_from
                        ],
                    },
                ),
                False,
            )

        return (
            self._make_result(
                rule_id="RG-DOCKER-002",
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="FROM instruction found",
                message=(
                    "The Dockerfile declares at least one base image."
                ),
                evidence=[
                    self._instruction_evidence(instruction)
                    for instruction in from_instructions
                ],
                recommendation=None,
                file_path=str(dockerfile_path),
                metadata={
                    "found_instructions": [
                        instruction.to_dict()
                        for instruction in from_instructions
                    ],
                },
            ),
            True,
        )

    def _check_from_position(
        self,
        project_path: Path,
        scan: DockerfileScan,
        valid_from: bool,
    ) -> CheckResult:
        dockerfile_path = project_path / scan.file_path

        if not valid_from:
            return self._make_result(
                rule_id="RG-DOCKER-003",
                status=CheckStatus.SKIPPED,
                risk_level=RiskLevel.INFO,
                title="FROM position check skipped",
                message=(
                    "A valid FROM instruction was not available, so "
                    "its position could not be checked."
                ),
                evidence=[
                    "RG-DOCKER-002 did not produce a valid FROM result."
                ],
                recommendation=None,
                file_path=str(dockerfile_path),
                metadata={
                    "skip_reason": "valid_from_unavailable",
                },
            )

        first_from_index = next(
            index
            for index, instruction in enumerate(
                scan.instructions
            )
            if instruction.keyword == "FROM"
        )

        first_from = scan.instructions[first_from_index]
        pre_from = scan.instructions[:first_from_index]
        invalid_pre_from = [
            instruction
            for instruction in pre_from
            if instruction.keyword != "ARG"
        ]
        invalid_pre_from_issues = [
            issue
            for issue in scan.issues
            if issue.line_number < first_from.start_line
        ]

        if invalid_pre_from or invalid_pre_from_issues:
            evidence = [
                self._instruction_evidence(instruction)
                for instruction in invalid_pre_from
            ]
            evidence.extend(
                (
                    "Dockerfile parse issue before FROM at line "
                    f"{issue.line_number}: {issue.message}"
                )
                for issue in invalid_pre_from_issues
            )

            return self._make_result(
                rule_id="RG-DOCKER-003",
                status=CheckStatus.FAILED,
                risk_level=RiskLevel.HIGH,
                title="Invalid content appears before FROM",
                message=(
                    "The Dockerfile contains invalid content or "
                    "instructions other than global ARG before the "
                    "first FROM instruction."
                ),
                evidence=evidence,
                recommendation=(
                    "Move the first FROM instruction before this "
                    "content or remove the malformed content. Only "
                    "parser directives, comments, and globally scoped "
                    "ARG instructions may precede the first FROM."
                ),
                file_path=str(dockerfile_path),
                metadata={
                    "pre_from_instructions": [
                        instruction.to_dict()
                        for instruction in pre_from
                    ],
                    "invalid_pre_from_instructions": [
                        instruction.to_dict()
                        for instruction in invalid_pre_from
                    ],
                    "invalid_pre_from_issues": [
                        issue.to_dict()
                        for issue in invalid_pre_from_issues
                    ],
                },
            )

        evidence = [
            self._instruction_evidence(first_from),
        ]

        evidence.extend(
            "Allowed global ARG before FROM: "
            + self._instruction_evidence(instruction)
            for instruction in pre_from
        )

        return self._make_result(
            rule_id="RG-DOCKER-003",
            status=CheckStatus.PASSED,
            risk_level=RiskLevel.INFO,
            title="FROM instruction position is valid",
            message=(
                "The first FROM instruction appears after only "
                "allowed Dockerfile prelude content."
            ),
            evidence=evidence,
            recommendation=None,
            file_path=str(dockerfile_path),
            metadata={
                "pre_from_instructions": [
                    instruction.to_dict()
                    for instruction in pre_from
                ],
                "invalid_pre_from_instructions": [],
                "invalid_pre_from_issues": [],
            },
        )

    def _check_instruction_presence(
        self,
        *,
        project_path: Path,
        scan: DockerfileScan,
        rule_id: str,
        keywords: tuple[str, ...],
        passed_title: str,
        passed_message: str,
        warning_title: str,
        warning_message: str,
        recommendation: str,
    ) -> CheckResult:
        dockerfile_path = project_path / scan.file_path
        matches = [
            instruction
            for instruction in scan.instructions
            if instruction.keyword in keywords
        ]

        if matches:
            return self._make_result(
                rule_id=rule_id,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title=passed_title,
                message=passed_message,
                evidence=[
                    self._instruction_evidence(instruction)
                    for instruction in matches
                ],
                recommendation=None,
                file_path=str(dockerfile_path),
                metadata={
                    "accepted_keywords": list(keywords),
                    "matched_instructions": [
                        instruction.to_dict()
                        for instruction in matches
                    ],
                },
            )

        return self._make_result(
            rule_id=rule_id,
            status=CheckStatus.WARNING,
            risk_level=RiskLevel.MEDIUM,
            title=warning_title,
            message=warning_message,
            evidence=[
                "No matching Docker instruction was found.",
                "Accepted instructions: " + ", ".join(keywords),
            ],
            recommendation=recommendation,
            file_path=str(dockerfile_path),
            metadata={
                "accepted_keywords": list(keywords),
                "matched_instructions": [],
            },
        )

    def _check_dependency_installation(
        self,
        project_path: Path,
        scan: DockerfileScan,
    ) -> CheckResult:
        dockerfile_path = project_path / scan.file_path

        matches = [
            instruction
            for instruction in scan.find_instructions("RUN")
            if any(
                pattern.search(instruction.arguments)
                for pattern in self.dependency_install_patterns
            )
        ]

        if matches:
            return self._make_result(
                rule_id="RG-DOCKER-006",
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="Python dependency installation found",
                message=(
                    "The Dockerfile contains a recognized Python "
                    "dependency installation command."
                ),
                evidence=[
                    self._instruction_evidence(instruction)
                    for instruction in matches
                ],
                recommendation=None,
                file_path=str(dockerfile_path),
                metadata={
                    "matched_instructions": [
                        instruction.to_dict()
                        for instruction in matches
                    ],
                },
            )

        return self._make_result(
            rule_id="RG-DOCKER-006",
            status=CheckStatus.WARNING,
            risk_level=RiskLevel.MEDIUM,
            title="Python dependency installation not detected",
            message=(
                "ReleaseGuard did not detect a recognized Python "
                "dependency installation command in a RUN instruction."
            ),
            evidence=[
                "Recognized command families: pip install, "
                "python -m pip install, uv sync, uv pip install, "
                "poetry install, pipenv install, and pdm install.",
            ],
            recommendation=(
                "If this image builds a Python application, install "
                "its declared dependencies during the image build."
            ),
            file_path=str(dockerfile_path),
            metadata={
                "matched_instructions": [],
                "policy_scope": "python_application_images",
            },
        )

    def _skipped_dependent_results(
        self,
        project_path: Path,
        *,
        reason: str,
        evidence: list[str],
        rule_ids: tuple[str, ...],
    ) -> list[CheckResult]:
        return [
            self._make_result(
                rule_id=rule_id,
                status=CheckStatus.SKIPPED,
                risk_level=RiskLevel.INFO,
                title=(
                    f"{self.rule_names[rule_id]} check skipped"
                ),
                message=reason,
                evidence=list(evidence),
                recommendation=None,
                file_path=str(project_path),
                metadata={
                    "skip_reason": reason,
                },
            )
            for rule_id in rule_ids
        ]

    def _find_root_dockerfile_intents(
        self,
        project_path: Path,
    ) -> tuple[list[RootDockerfileIntent], list[str]]:
        intents: list[RootDockerfileIntent] = []
        diagnostics: list[str] = []

        for file_name in self.compose_file_names:
            compose_path = project_path / file_name

            if not compose_path.is_file():
                continue

            try:
                source = compose_path.read_text(
                    encoding="utf-8-sig"
                )
                document = yaml.safe_load(source)
            except (
                OSError,
                UnicodeDecodeError,
                yaml.YAMLError,
            ) as error:
                diagnostics.append(
                    f"Could not parse {file_name}: "
                    f"{type(error).__name__}"
                )
                continue

            if not isinstance(document, dict):
                diagnostics.append(
                    f"Compose file {file_name} does not contain "
                    "a mapping document."
                )
                continue

            services = document.get("services")

            if not isinstance(services, dict):
                continue

            for service_name, service in services.items():
                if not isinstance(service, dict):
                    continue

                if "build" not in service:
                    continue

                intent = self._intent_from_build(
                    compose_file=file_name,
                    service_name=str(service_name),
                    build=service.get("build"),
                )

                if intent is not None:
                    intents.append(intent)

        return intents, diagnostics

    def _intent_from_build(
        self,
        *,
        compose_file: str,
        service_name: str,
        build: Any,
    ) -> RootDockerfileIntent | None:
        if isinstance(build, str):
            if not self._is_project_root_context(build):
                return None

            return RootDockerfileIntent(
                compose_file=compose_file,
                service_name=service_name,
                build_context=build,
                dockerfile=None,
            )

        if not isinstance(build, dict):
            return None

        if "dockerfile_inline" in build:
            return None

        context = build.get("context", ".")

        if not isinstance(context, str):
            return None

        if not self._is_project_root_context(context):
            return None

        dockerfile = build.get("dockerfile")

        if dockerfile is not None:
            if not isinstance(dockerfile, str):
                return None

            if not self._is_default_dockerfile(dockerfile):
                return None

        return RootDockerfileIntent(
            compose_file=compose_file,
            service_name=service_name,
            build_context=context,
            dockerfile=dockerfile,
        )

    def _is_project_root_context(self, value: str) -> bool:
        normalized = self._normalize_local_path(value)
        return normalized == "."

    def _is_default_dockerfile(self, value: str) -> bool:
        normalized = self._normalize_local_path(value)
        return normalized == "Dockerfile"

    def _normalize_local_path(
        self,
        value: str,
    ) -> str | None:
        cleaned = value.strip()

        if not cleaned:
            return None

        if "${" in cleaned or "://" in cleaned:
            return None

        cleaned = cleaned.replace("\\", "/")

        if cleaned.startswith("/"):
            return None

        if re.match(r"^[A-Za-z]:", cleaned):
            return None

        return posixpath.normpath(cleaned)

    def _instruction_evidence(
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

        summary = (
            f"{instruction.keyword} "
            f"{instruction.arguments}"
        ).rstrip()

        return (
            f"Found Docker instruction at Dockerfile:{location}: "
            f"`{summary}`"
        )

    def _issue_evidence(
        self,
        scan: DockerfileScan,
    ) -> list[str]:
        return [
            f"Dockerfile parse issue at line "
            f"{issue.line_number}: {issue.message}"
            for issue in scan.issues
        ]

    def _make_result(
        self,
        *,
        rule_id: str,
        status: CheckStatus,
        risk_level: RiskLevel,
        title: str,
        message: str,
        evidence: list[str],
        recommendation: str | None,
        file_path: str,
        metadata: dict[str, Any],
    ) -> CheckResult:
        result_metadata: dict[str, Any] = dict(
            self.rule_metadata[rule_id]
        )
        result_metadata.update(metadata)

        return CheckResult(
            checker_name=self.name,
            status=status,
            risk_level=risk_level,
            title=title,
            message=message,
            evidence=evidence,
            recommendation=recommendation,
            rule_id=rule_id,
            rule_source=self.rule_sources[rule_id],
            file_path=file_path,
            metadata=result_metadata,
        )
