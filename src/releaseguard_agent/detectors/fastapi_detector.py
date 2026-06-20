import ast
from dataclasses import dataclass
from pathlib import Path

from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.scanners.python_dependency_scanner import (
    DependencyMatch,
    PythonDependencyScanner,
)


@dataclass(frozen=True)
class SourceMatch:
    """One FastAPI source-code match."""

    file_path: str
    line_number: int
    source_line: str
    match_type: str
    target_name: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "source_line": self.source_line,
            "match_type": self.match_type,
            "target_name": self.target_name,
        }


@dataclass(frozen=True)
class FastAPIScan:
    """Aggregated FastAPI source scan result."""

    usage_matches: tuple[SourceMatch, ...]
    app_matches: tuple[SourceMatch, ...]
    checked_file_count: int
    parse_errors: tuple[str, ...]


class FastAPIDetector(BaseChecker):
    """Detect explicit FastAPI usage and application instances."""

    name = "fastapi_detector"
    description = (
        "Detects FastAPI usage, dependency declarations, "
        "and explicit application instances."
    )

    ignored_directories: tuple[str, ...] = (
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        "outputs",
        "site-packages",
        "tests",
    )

    dependency_scanner = PythonDependencyScanner()

    def run(self, project_path: Path) -> list[CheckResult]:
        """Run FastAPI detection against a target project."""
        project_path = Path(project_path)
        source_scan = self._scan_python_sources(project_path)

        if not source_scan.usage_matches:
            return self._build_not_detected_results(
                project_path,
                source_scan,
            )

        dependency_matches = self.dependency_scanner.find_matches(
            project_path,
            "fastapi",
        )

        return [
            self._build_dependency_result(
                project_path=project_path,
                source_scan=source_scan,
                dependency_matches=dependency_matches,
            ),
            self._build_app_instance_result(
                project_path=project_path,
                source_scan=source_scan,
            ),
        ]

    def _build_not_detected_results(
        self,
        project_path: Path,
        source_scan: FastAPIScan,
    ) -> list[CheckResult]:
        metadata = {
            "fastapi_usage_detected": False,
            "checked_python_file_count": source_scan.checked_file_count,
            "usage_matches": [],
            "app_matches": [],
            "parse_errors": list(source_scan.parse_errors),
            "support_level": "conditional",
            "blocking_policy": "conditional",
            "evidence_type": "ast_scan",
        }

        return [
            CheckResult(
                checker_name=self.name,
                status=CheckStatus.SKIPPED,
                risk_level=RiskLevel.INFO,
                title="FastAPI dependency check not applicable",
                message="No FastAPI source usage was detected.",
                evidence=[
                    (
                        "Checked production Python files: "
                        f"{source_scan.checked_file_count}"
                    )
                ],
                recommendation=None,
                rule_id="RG-FASTAPI-001",
                rule_source=(
                    "FastAPI Testing; "
                    "ReleaseGuard dependency policy"
                ),
                file_path=str(project_path),
                metadata=metadata.copy(),
            ),
            CheckResult(
                checker_name=self.name,
                status=CheckStatus.SKIPPED,
                risk_level=RiskLevel.INFO,
                title="FastAPI app detection not applicable",
                message="No FastAPI source usage was detected.",
                evidence=[
                    (
                        "Checked production Python files: "
                        f"{source_scan.checked_file_count}"
                    )
                ],
                recommendation=None,
                rule_id="RG-FASTAPI-002",
                rule_source="FastAPI Testing",
                file_path=str(project_path),
                metadata=metadata.copy(),
            ),
        ]

    def _build_dependency_result(
        self,
        *,
        project_path: Path,
        source_scan: FastAPIScan,
        dependency_matches: list[DependencyMatch],
    ) -> CheckResult:
        metadata = {
            "fastapi_usage_detected": True,
            "checked_python_file_count": source_scan.checked_file_count,
            "checked_dependency_files": list(
                self.dependency_scanner.dependency_files
            ),
            "usage_matches": [
                match.to_dict()
                for match in source_scan.usage_matches
            ],
            "dependency_matches": [
                match.to_dict()
                for match in dependency_matches
            ],
            "parse_errors": list(source_scan.parse_errors),
            "support_level": "releaseguard-default",
            "blocking_policy": "block",
            "evidence_type": "dependency_line",
        }

        usage_evidence = self._format_source_evidence(
            source_scan.usage_matches,
            "FastAPI usage",
        )

        if dependency_matches:
            dependency_evidence = [
                self._format_dependency_match(match)
                for match in dependency_matches[:10]
            ]

            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="FastAPI dependency declared",
                message=(
                    "FastAPI source usage and a FastAPI dependency "
                    "declaration were found."
                ),
                evidence=usage_evidence + dependency_evidence,
                recommendation=None,
                rule_id="RG-FASTAPI-001",
                rule_source=(
                    "FastAPI Testing; "
                    "ReleaseGuard dependency policy"
                ),
                file_path=str(project_path),
                metadata=metadata,
            )

        return CheckResult(
            checker_name=self.name,
            status=CheckStatus.FAILED,
            risk_level=RiskLevel.HIGH,
            title="FastAPI dependency missing",
            message=(
                "FastAPI source usage was detected, but FastAPI was not "
                "found in a supported dependency declaration file."
            ),
            evidence=[
                *usage_evidence,
                (
                    "Checked dependency files: "
                    + ", ".join(
                        self.dependency_scanner.dependency_files
                    )
                ),
                "No FastAPI dependency declaration was found.",
            ],
            recommendation=(
                "Declare FastAPI as a runtime dependency, for example "
                "fastapi==<approved-version> in requirements.txt or "
                "FastAPI in project.dependencies in pyproject.toml."
            ),
            rule_id="RG-FASTAPI-001",
            rule_source=(
                "FastAPI Testing; "
                "ReleaseGuard dependency policy"
            ),
            file_path=str(project_path),
            metadata=metadata,
        )

    def _build_app_instance_result(
        self,
        *,
        project_path: Path,
        source_scan: FastAPIScan,
    ) -> CheckResult:
        metadata = {
            "fastapi_usage_detected": True,
            "app_instance_detected": bool(source_scan.app_matches),
            "checked_python_file_count": source_scan.checked_file_count,
            "usage_matches": [
                match.to_dict()
                for match in source_scan.usage_matches
            ],
            "app_matches": [
                match.to_dict()
                for match in source_scan.app_matches
            ],
            "parse_errors": list(source_scan.parse_errors),
            "support_level": "source-backed",
            "blocking_policy": "block",
            "evidence_type": "matched_lines",
        }

        if source_scan.app_matches:
            first_match = source_scan.app_matches[0]

            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="FastAPI application instance found",
                message=(
                    "An explicit FastAPI application instance was detected."
                ),
                evidence=self._format_source_evidence(
                    source_scan.app_matches,
                    "FastAPI app",
                ),
                recommendation=None,
                rule_id="RG-FASTAPI-002",
                rule_source="FastAPI Testing",
                file_path=str(project_path / first_match.file_path),
                metadata=metadata,
            )

        return CheckResult(
            checker_name=self.name,
            status=CheckStatus.FAILED,
            risk_level=RiskLevel.HIGH,
            title="FastAPI application instance missing",
            message=(
                "FastAPI source usage was detected, but no explicit "
                "FastAPI() application assignment was found."
            ),
            evidence=[
                *self._format_source_evidence(
                    source_scan.usage_matches,
                    "FastAPI usage",
                ),
                "No assignment such as app = FastAPI() was detected.",
            ],
            recommendation=(
                "Expose an explicit FastAPI application instance, such as "
                "app = FastAPI(), or extend the detector if the project "
                "intentionally uses an application factory."
            ),
            rule_id="RG-FASTAPI-002",
            rule_source="FastAPI Testing",
            file_path=str(project_path),
            metadata=metadata,
        )

    def _scan_python_sources(
        self,
        project_path: Path,
    ) -> FastAPIScan:
        usage_matches: list[SourceMatch] = []
        app_matches: list[SourceMatch] = []
        parse_errors: list[str] = []
        checked_file_count = 0

        python_files = sorted(
            project_path.rglob("*.py"),
            key=lambda path: str(path),
        )

        for python_file in python_files:
            if self._should_ignore_path(python_file):
                continue

            checked_file_count += 1
            file_usage, file_apps, parse_error = self._scan_python_file(
                python_file,
                project_path,
            )
            usage_matches.extend(file_usage)
            app_matches.extend(file_apps)

            if parse_error is not None:
                parse_errors.append(parse_error)

        return FastAPIScan(
            usage_matches=tuple(
                self._deduplicate_source_matches(usage_matches)
            ),
            app_matches=tuple(
                self._deduplicate_source_matches(app_matches)
            ),
            checked_file_count=checked_file_count,
            parse_errors=tuple(parse_errors),
        )

    def _scan_python_file(
        self,
        python_file: Path,
        project_path: Path,
    ) -> tuple[list[SourceMatch], list[SourceMatch], str | None]:
        relative_path = self._relative_path(python_file, project_path)

        try:
            source = python_file.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as error:
            return [], [], f"{relative_path}: {type(error).__name__}: {error}"

        try:
            tree = ast.parse(source, filename=str(python_file))
        except SyntaxError as error:
            return (
                [],
                [],
                (
                    f"{relative_path}:{error.lineno or 0}: "
                    f"SyntaxError: {error.msg}"
                ),
            )

        source_lines = source.splitlines()
        constructor_names: set[str] = set()
        module_names: set[str] = set()
        usage_matches: list[SourceMatch] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                fastapi_aliases = [
                    alias
                    for alias in node.names
                    if alias.name == "fastapi"
                    or alias.name.startswith("fastapi.")
                ]

                if fastapi_aliases:
                    usage_matches.append(
                        self._make_source_match(
                            relative_path=relative_path,
                            node=node,
                            source_lines=source_lines,
                            match_type="fastapi_import",
                        )
                    )

                for alias in fastapi_aliases:
                    module_names.add(
                        alias.asname or alias.name.split(".")[0]
                    )

            if isinstance(node, ast.ImportFrom):
                module_name = node.module or ""

                if (
                    module_name == "fastapi"
                    or module_name.startswith("fastapi.")
                ):
                    usage_matches.append(
                        self._make_source_match(
                            relative_path=relative_path,
                            node=node,
                            source_lines=source_lines,
                            match_type="fastapi_import",
                        )
                    )

                    for alias in node.names:
                        if alias.name == "FastAPI":
                            constructor_names.add(
                                alias.asname or alias.name
                            )

        app_matches: list[SourceMatch] = []

        for node in ast.walk(tree):
            value: ast.expr | None = None
            targets: list[ast.expr] = []

            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                value = node.value
                targets = [node.target]

            if not isinstance(value, ast.Call):
                continue

            if not self._is_fastapi_constructor_call(
                value,
                constructor_names,
                module_names,
            ):
                continue

            target_names = [
                target_name
                for target in targets
                for target_name in self._target_names(target)
            ]

            app_matches.append(
                self._make_source_match(
                    relative_path=relative_path,
                    node=node,
                    source_lines=source_lines,
                    match_type="fastapi_app_instance",
                    target_name=", ".join(target_names) or None,
                )
            )

        return usage_matches, app_matches, None

    def _is_fastapi_constructor_call(
        self,
        call: ast.Call,
        constructor_names: set[str],
        module_names: set[str],
    ) -> bool:
        function = call.func

        if isinstance(function, ast.Name):
            return function.id in constructor_names

        if not isinstance(function, ast.Attribute):
            return False

        return (
            function.attr == "FastAPI"
            and isinstance(function.value, ast.Name)
            and function.value.id in module_names
        )

    def _target_names(self, target: ast.expr) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]

        if isinstance(target, (ast.Tuple, ast.List)):
            return [
                name
                for element in target.elts
                for name in self._target_names(element)
            ]

        if isinstance(target, ast.Attribute):
            return [ast.unparse(target)]

        return []

    def _make_source_match(
        self,
        *,
        relative_path: str,
        node: ast.AST,
        source_lines: list[str],
        match_type: str,
        target_name: str | None = None,
    ) -> SourceMatch:
        line_number = int(getattr(node, "lineno", 0))
        source_line = ""

        if 1 <= line_number <= len(source_lines):
            source_line = source_lines[line_number - 1].strip()

        return SourceMatch(
            file_path=relative_path,
            line_number=line_number,
            source_line=source_line,
            match_type=match_type,
            target_name=target_name,
        )

    def _format_source_evidence(
        self,
        matches: tuple[SourceMatch, ...],
        label: str,
    ) -> list[str]:
        return [
            (
                f"{label}: {match.file_path}:{match.line_number}: "
                f"{match.source_line}"
            )
            for match in matches[:10]
        ]

    def _format_dependency_match(
        self,
        match: DependencyMatch,
    ) -> str:
        location = match.file_path

        if match.line_number is not None:
            location += f":{match.line_number}"

        return (
            f"FastAPI dependency: {location}: "
            f"{match.declaration}"
        )

    def _deduplicate_source_matches(
        self,
        matches: list[SourceMatch],
    ) -> list[SourceMatch]:
        return list(dict.fromkeys(matches))

    def _should_ignore_path(self, path: Path) -> bool:
        return any(
            part in self.ignored_directories
            for part in path.parts
        )

    def _relative_path(
        self,
        path: Path,
        project_path: Path,
    ) -> str:
        try:
            return str(path.relative_to(project_path))
        except ValueError:
            return str(path)