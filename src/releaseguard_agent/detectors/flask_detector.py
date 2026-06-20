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
class RunCall:
    """One local or imported application run call."""

    match: SourceMatch
    receiver_name: str
    local_app: bool
    imported_module: str | None
    imported_name: str | None
    debug_mode: str


@dataclass(frozen=True)
class FlaskScan:
    usage_matches: tuple[SourceMatch, ...]
    app_matches: tuple[SourceMatch, ...]
    run_matches: tuple[SourceMatch, ...]
    debug_true_matches: tuple[SourceMatch, ...]
    dynamic_debug_matches: tuple[SourceMatch, ...]
    checked_file_count: int
    parse_errors: tuple[str, ...]


class FlaskDetector(BaseChecker):
    """Detect Flask application structure and release risks."""

    name = "flask_detector"
    description = (
        "Detects Flask usage, dependency declarations, application "
        "instances, development-server usage, and debug mode."
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
        project_path = Path(project_path)
        source_scan = self._scan_python_sources(project_path)

        if not source_scan.usage_matches:
            return self._build_not_detected_results(
                project_path,
                source_scan,
            )

        dependency_matches = self.dependency_scanner.find_matches(
            project_path,
            "flask",
        )

        return [
            self._build_dependency_result(
                project_path,
                source_scan,
                dependency_matches,
            ),
            self._build_app_result(project_path, source_scan),
            self._build_development_server_result(
                project_path,
                source_scan,
            ),
            self._build_debug_result(project_path, source_scan),
        ]

    def _build_not_detected_results(
        self,
        project_path: Path,
        scan: FlaskScan,
    ) -> list[CheckResult]:
        specifications = (
            (
                "RG-FLASK-001",
                "Flask dependency check not applicable",
                "Flask Installation; ReleaseGuard dependency policy",
                "releaseguard-default",
                "block",
                "dependency_line",
            ),
            (
                "RG-FLASK-002",
                "Flask app detection not applicable",
                "Flask Quickstart",
                "source-backed",
                "block",
                "matched_lines",
            ),
            (
                "RG-FLASK-003",
                "Flask development-server check not applicable",
                "Flask Quickstart; Flask Deploying To Production",
                "source-backed",
                "conditional",
                "matched_lines",
            ),
            (
                "RG-SEC-002",
                "Flask debug-mode check not applicable",
                "Flask Debugging; Flask Quickstart",
                "source-backed",
                "block",
                "matched_lines",
            ),
        )

        return [
            CheckResult(
                checker_name=self.name,
                status=CheckStatus.SKIPPED,
                risk_level=RiskLevel.INFO,
                title=title,
                message="No Flask source usage was detected.",
                evidence=[
                    (
                        "Checked production Python files: "
                        f"{scan.checked_file_count}"
                    )
                ],
                recommendation=None,
                rule_id=rule_id,
                rule_source=rule_source,
                file_path=str(project_path),
                metadata=self._metadata(
                    scan,
                    support_level=support_level,
                    blocking_policy=blocking_policy,
                    evidence_type=evidence_type,
                ),
            )
            for (
                rule_id,
                title,
                rule_source,
                support_level,
                blocking_policy,
                evidence_type,
            ) in specifications
        ]

    def _build_dependency_result(
        self,
        project_path: Path,
        scan: FlaskScan,
        dependency_matches: list[DependencyMatch],
    ) -> CheckResult:
        metadata = self._metadata(
            scan,
            support_level="releaseguard-default",
            blocking_policy="block",
            evidence_type="dependency_line",
            dependency_matches=dependency_matches,
        )
        usage_evidence = self._format_source_evidence(
            scan.usage_matches,
            "Flask usage",
        )

        if dependency_matches:
            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="Flask dependency declared",
                message=(
                    "Flask source usage and a Flask dependency "
                    "declaration were found."
                ),
                evidence=[
                    *usage_evidence,
                    *[
                        self._format_dependency_match(match)
                        for match in dependency_matches[:10]
                    ],
                ],
                recommendation=None,
                rule_id="RG-FLASK-001",
                rule_source=(
                    "Flask Installation; "
                    "ReleaseGuard dependency policy"
                ),
                file_path=str(project_path),
                metadata=metadata,
            )

        return CheckResult(
            checker_name=self.name,
            status=CheckStatus.FAILED,
            risk_level=RiskLevel.HIGH,
            title="Flask dependency missing",
            message=(
                "Flask source usage was detected, but Flask was not "
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
                "No Flask dependency declaration was found.",
            ],
            recommendation=(
                "Declare Flask as a runtime dependency, for example "
                "Flask==<approved-version> in requirements.txt or "
                "Flask in project.dependencies in pyproject.toml."
            ),
            rule_id="RG-FLASK-001",
            rule_source=(
                "Flask Installation; ReleaseGuard dependency policy"
            ),
            file_path=str(project_path),
            metadata=metadata,
        )

    def _build_app_result(
        self,
        project_path: Path,
        scan: FlaskScan,
    ) -> CheckResult:
        metadata = self._metadata(
            scan,
            support_level="source-backed",
            blocking_policy="block",
            evidence_type="matched_lines",
        )

        if scan.app_matches:
            first_match = scan.app_matches[0]

            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
                title="Flask application instance found",
                message=(
                    "An explicit Flask application instance was detected."
                ),
                evidence=self._format_source_evidence(
                    scan.app_matches,
                    "Flask app",
                ),
                recommendation=None,
                rule_id="RG-FLASK-002",
                rule_source="Flask Quickstart",
                file_path=str(project_path / first_match.file_path),
                metadata=metadata,
            )

        return CheckResult(
            checker_name=self.name,
            status=CheckStatus.FAILED,
            risk_level=RiskLevel.HIGH,
            title="Flask application instance missing",
            message=(
                "Flask source usage was detected, but no explicit "
                "Flask(...) application assignment was found."
            ),
            evidence=[
                *self._format_source_evidence(
                    scan.usage_matches,
                    "Flask usage",
                ),
                "No assignment such as app = Flask(__name__) was detected.",
            ],
            recommendation=(
                "Expose an explicit Flask application instance, or extend "
                "the detector if the project intentionally uses an "
                "application factory."
            ),
            rule_id="RG-FLASK-002",
            rule_source="Flask Quickstart",
            file_path=str(project_path),
            metadata=metadata,
        )

    def _build_development_server_result(
        self,
        project_path: Path,
        scan: FlaskScan,
    ) -> CheckResult:
        metadata = self._metadata(
            scan,
            support_level="source-backed",
            blocking_policy="conditional",
            evidence_type="matched_lines",
        )

        if scan.run_matches:
            first_match = scan.run_matches[0]

            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.WARNING,
                risk_level=RiskLevel.MEDIUM,
                title="Flask development-server startup found",
                message=(
                    "An application run() call was detected. Flask's "
                    "built-in server is intended for development and "
                    "testing, so deployment configuration requires review."
                ),
                evidence=self._format_source_evidence(
                    scan.run_matches,
                    "Flask run call",
                ),
                recommendation=(
                    "Use a production WSGI server for release deployment "
                    "and keep app.run() limited to local development."
                ),
                rule_id="RG-FLASK-003",
                rule_source=(
                    "Flask Quickstart; "
                    "Flask Deploying To Production"
                ),
                file_path=str(project_path / first_match.file_path),
                metadata=metadata,
            )

        return CheckResult(
            checker_name=self.name,
            status=CheckStatus.PASSED,
            risk_level=RiskLevel.INFO,
            title="No Flask development-server startup found",
            message=(
                "No run() call on an explicitly detected Flask "
                "application was found."
            ),
            evidence=[
                "No Flask application run() call was detected."
            ],
            recommendation=None,
            rule_id="RG-FLASK-003",
            rule_source=(
                "Flask Quickstart; Flask Deploying To Production"
            ),
            file_path=str(project_path),
            metadata=metadata,
        )

    def _build_debug_result(
        self,
        project_path: Path,
        scan: FlaskScan,
    ) -> CheckResult:
        metadata = self._metadata(
            scan,
            support_level="source-backed",
            blocking_policy="block",
            evidence_type="matched_lines",
        )

        if scan.debug_true_matches:
            first_match = scan.debug_true_matches[0]

            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.FAILED,
                risk_level=RiskLevel.HIGH,
                title="Flask debug mode explicitly enabled",
                message=(
                    "A Flask application run() call explicitly enables "
                    "debug mode."
                ),
                evidence=self._format_source_evidence(
                    scan.debug_true_matches,
                    "Flask debug mode",
                ),
                recommendation=(
                    "Remove debug=True from release startup code and "
                    "ensure the interactive debugger cannot be enabled "
                    "in production."
                ),
                rule_id="RG-SEC-002",
                rule_source="Flask Debugging; Flask Quickstart",
                file_path=str(project_path / first_match.file_path),
                metadata=metadata,
            )

        if scan.dynamic_debug_matches:
            first_match = scan.dynamic_debug_matches[0]

            return CheckResult(
                checker_name=self.name,
                status=CheckStatus.WARNING,
                risk_level=RiskLevel.MEDIUM,
                title="Flask debug mode uses a dynamic value",
                message=(
                    "The Flask debug argument is dynamically configured, "
                    "so its release value cannot be proven statically."
                ),
                evidence=self._format_source_evidence(
                    scan.dynamic_debug_matches,
                    "Dynamic Flask debug value",
                ),
                recommendation=(
                    "Ensure the release environment resolves the debug "
                    "setting to False and add deployment validation."
                ),
                rule_id="RG-SEC-002",
                rule_source="Flask Debugging; Flask Quickstart",
                file_path=str(project_path / first_match.file_path),
                metadata=metadata,
            )

        return CheckResult(
            checker_name=self.name,
            status=CheckStatus.PASSED,
            risk_level=RiskLevel.INFO,
            title="Flask debug mode not explicitly enabled",
            message=(
                "No explicit debug=True or dynamic debug argument was "
                "found on a detected Flask application run() call."
            ),
            evidence=[
                "No risky Flask debug argument was detected."
            ],
            recommendation=None,
            rule_id="RG-SEC-002",
            rule_source="Flask Debugging; Flask Quickstart",
            file_path=str(project_path),
            metadata=metadata,
        )

    def _scan_python_sources(self, project_path: Path) -> FlaskScan:
        usage_matches: list[SourceMatch] = []
        app_matches: list[SourceMatch] = []
        run_calls: list[RunCall] = []
        run_matches: list[SourceMatch] = []
        debug_true_matches: list[SourceMatch] = []
        dynamic_debug_matches: list[SourceMatch] = []
        parse_errors: list[str] = []
        checked_file_count = 0

        for python_file in sorted(
            project_path.rglob("*.py"),
            key=lambda path: str(path),
        ):
            if self._should_ignore_path(python_file):
                continue

            checked_file_count += 1
            (
                file_usage,
                file_apps,
                file_run_calls,
                parse_error,
            ) = self._scan_python_file(python_file, project_path)

            usage_matches.extend(file_usage)
            app_matches.extend(file_apps)
            run_calls.extend(file_run_calls)

            if parse_error is not None:
                parse_errors.append(parse_error)

        app_definitions = self._app_definitions(app_matches)

        for run_call in run_calls:
            imported_app = (
                run_call.imported_module,
                run_call.imported_name,
            ) in app_definitions

            if not run_call.local_app and not imported_app:
                continue

            run_matches.append(run_call.match)

            if run_call.debug_mode == "true":
                debug_true_matches.append(
                    self._retag_match(
                        run_call.match,
                        "flask_debug_true",
                    )
                )
            elif run_call.debug_mode == "dynamic":
                dynamic_debug_matches.append(
                    self._retag_match(
                        run_call.match,
                        "flask_debug_dynamic",
                    )
                )

        return FlaskScan(
            usage_matches=tuple(self._deduplicate(usage_matches)),
            app_matches=tuple(self._deduplicate(app_matches)),
            run_matches=tuple(self._deduplicate(run_matches)),
            debug_true_matches=tuple(
                self._deduplicate(debug_true_matches)
            ),
            dynamic_debug_matches=tuple(
                self._deduplicate(dynamic_debug_matches)
            ),
            checked_file_count=checked_file_count,
            parse_errors=tuple(parse_errors),
        )

    def _scan_python_file(
        self,
        python_file: Path,
        project_path: Path,
    ) -> tuple[
        list[SourceMatch],
        list[SourceMatch],
        list[RunCall],
        str | None,
    ]:
        relative_path = self._relative_path(
            python_file,
            project_path,
        )

        try:
            source = python_file.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as error:
            return (
                [],
                [],
                [],
                f"{relative_path}: {type(error).__name__}: {error}",
            )

        try:
            tree = ast.parse(source, filename=str(python_file))
        except SyntaxError as error:
            return (
                [],
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
        imported_names: dict[str, tuple[str, str]] = {}
        usage_matches: list[SourceMatch] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                aliases = [
                    alias
                    for alias in node.names
                    if alias.name == "flask"
                    or alias.name.startswith("flask.")
                ]

                if aliases:
                    usage_matches.append(
                        self._make_match(
                            relative_path,
                            node,
                            source_lines,
                            "flask_import",
                        )
                    )

                for alias in aliases:
                    module_names.add(
                        alias.asname or alias.name.split(".")[0]
                    )

            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                resolved_module = self._resolve_import_module(
                    relative_path,
                    node,
                )

                if resolved_module:
                    for alias in node.names:
                        if alias.name == "*":
                            continue

                        imported_names[
                            alias.asname or alias.name
                        ] = (resolved_module, alias.name)

                if (
                    module_name == "flask"
                    or module_name.startswith("flask.")
                ):
                    usage_matches.append(
                        self._make_match(
                            relative_path,
                            node,
                            source_lines,
                            "flask_import",
                        )
                    )

                    for alias in node.names:
                        if alias.name == "Flask":
                            constructor_names.add(
                                alias.asname or alias.name
                            )

        app_matches: list[SourceMatch] = []
        app_names: set[str] = set()

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

            if not self._is_flask_constructor_call(
                value,
                constructor_names,
                module_names,
            ):
                continue

            target_names = [
                name
                for target in targets
                for name in self._target_names(target)
            ]
            app_names.update(target_names)

            app_matches.append(
                self._make_match(
                    relative_path,
                    node,
                    source_lines,
                    "flask_app_instance",
                    ", ".join(target_names) or None,
                )
            )

        run_calls: list[RunCall] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            receiver_name = self._run_receiver_name(node)

            if receiver_name is None:
                continue

            local_app = receiver_name in app_names
            import_binding = imported_names.get(receiver_name)

            if not local_app and import_binding is None:
                continue

            imported_module: str | None = None
            imported_name: str | None = None

            if import_binding is not None:
                imported_module, imported_name = import_binding

            run_calls.append(
                RunCall(
                    match=self._make_match(
                        relative_path,
                        node,
                        source_lines,
                        "flask_development_server",
                        receiver_name,
                    ),
                    receiver_name=receiver_name,
                    local_app=local_app,
                    imported_module=imported_module,
                    imported_name=imported_name,
                    debug_mode=self._debug_mode(node),
                )
            )

        return (
            usage_matches,
            app_matches,
            run_calls,
            None,
        )

    def _app_definitions(
        self,
        app_matches: list[SourceMatch],
    ) -> set[tuple[str, str]]:
        definitions: set[tuple[str, str]] = set()

        for match in app_matches:
            if match.target_name is None:
                continue

            module_names = self._module_names_for_source(
                match.file_path
            )

            for target_name in match.target_name.split(","):
                normalized_target = target_name.strip()

                if not normalized_target.isidentifier():
                    continue

                definitions.update(
                    (module_name, normalized_target)
                    for module_name in module_names
                )

        return definitions

    def _module_names_for_source(
        self,
        file_path: str,
    ) -> set[str]:
        path = Path(file_path).with_suffix("")
        parts = list(path.parts)

        if parts and parts[-1] == "__init__":
            parts.pop()

        module_names: set[str] = set()

        if parts:
            module_names.add(".".join(parts))

        if parts and parts[0] == "src" and len(parts) > 1:
            module_names.add(".".join(parts[1:]))

        return module_names

    def _resolve_import_module(
        self,
        file_path: str,
        node: ast.ImportFrom,
    ) -> str:
        module_name = node.module or ""

        if node.level == 0:
            return module_name

        source_path = Path(file_path).with_suffix("")
        source_parts = list(source_path.parts)

        if source_parts and source_parts[0] == "src":
            source_parts = source_parts[1:]

        if source_parts:
            source_parts.pop()

        parent_levels = max(node.level - 1, 0)

        if parent_levels:
            source_parts = source_parts[:-parent_levels]

        if module_name:
            source_parts.extend(module_name.split("."))

        return ".".join(source_parts)

    def _debug_mode(self, call: ast.Call) -> str:
        for keyword in call.keywords:
            if keyword.arg != "debug":
                continue

            if (
                isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
            ):
                return "true"

            if (
                isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
            ):
                return "false"

            return "dynamic"

        return "absent"

    def _retag_match(
        self,
        match: SourceMatch,
        match_type: str,
    ) -> SourceMatch:
        return SourceMatch(
            file_path=match.file_path,
            line_number=match.line_number,
            source_line=match.source_line,
            match_type=match_type,
            target_name=match.target_name,
        )
    def _is_flask_constructor_call(
        self,
        call: ast.Call,
        constructor_names: set[str],
        module_names: set[str],
    ) -> bool:
        function = call.func

        if isinstance(function, ast.Name):
            return function.id in constructor_names

        return (
            isinstance(function, ast.Attribute)
            and function.attr == "Flask"
            and self._root_name(function) in module_names
        )

    def _root_name(self, node: ast.AST) -> str | None:
        current = node

        while isinstance(current, ast.Attribute):
            current = current.value

        if isinstance(current, ast.Name):
            return current.id

        return None

    def _run_receiver_name(self, call: ast.Call) -> str | None:
        function = call.func

        if not (
            isinstance(function, ast.Attribute)
            and function.attr == "run"
        ):
            return None

        return ast.unparse(function.value)

    def _target_names(self, target: ast.expr) -> list[str]:
        if isinstance(target, (ast.Name, ast.Attribute)):
            return [ast.unparse(target)]

        if isinstance(target, (ast.Tuple, ast.List)):
            return [
                name
                for element in target.elts
                for name in self._target_names(element)
            ]

        return []

    def _make_match(
        self,
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

    def _metadata(
        self,
        scan: FlaskScan,
        *,
        support_level: str,
        blocking_policy: str,
        evidence_type: str,
        dependency_matches: list[DependencyMatch] | None = None,
    ) -> dict[str, object]:
        return {
            "flask_usage_detected": bool(scan.usage_matches),
            "app_instance_detected": bool(scan.app_matches),
            "development_server_detected": bool(scan.run_matches),
            "debug_true_detected": bool(scan.debug_true_matches),
            "dynamic_debug_detected": bool(
                scan.dynamic_debug_matches
            ),
            "checked_python_file_count": scan.checked_file_count,
            "checked_dependency_files": list(
                self.dependency_scanner.dependency_files
            ),
            "usage_matches": [
                match.to_dict() for match in scan.usage_matches
            ],
            "app_matches": [
                match.to_dict() for match in scan.app_matches
            ],
            "run_matches": [
                match.to_dict() for match in scan.run_matches
            ],
            "debug_true_matches": [
                match.to_dict()
                for match in scan.debug_true_matches
            ],
            "dynamic_debug_matches": [
                match.to_dict()
                for match in scan.dynamic_debug_matches
            ],
            "dependency_matches": [
                match.to_dict()
                for match in (dependency_matches or [])
            ],
            "parse_errors": list(scan.parse_errors),
            "support_level": support_level,
            "blocking_policy": blocking_policy,
            "evidence_type": evidence_type,
        }

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
            f"Flask dependency: {location}: "
            f"{match.declaration}"
        )

    def _deduplicate(
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
