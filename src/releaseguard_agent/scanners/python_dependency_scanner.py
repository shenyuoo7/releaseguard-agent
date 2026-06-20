import ast
import configparser
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DependencyMatch:
    """One matching Python dependency declaration."""

    file_path: str
    line_number: int | None
    declaration: str

    def to_dict(self) -> dict[str, object]:
        """Convert the match to serializable evidence."""

        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "declaration": self.declaration,
        }


class PythonDependencyScanner:
    """Find package declarations in supported Python dependency files."""

    dependency_files: tuple[str, ...] = (
        "requirements.txt",
        "pyproject.toml",
        "Pipfile",
        "poetry.lock",
        "uv.lock",
        "setup.py",
        "setup.cfg",
    )

    def find_matches(
        self,
        project_path: Path,
        package_name: str,
    ) -> list[DependencyMatch]:
        """Return declarations matching a package in the project root."""

        project_path = Path(project_path)
        normalized_name = self._validate_package_name(package_name)
        matches: list[DependencyMatch] = []

        for file_name in self.dependency_files:
            dependency_file = project_path / file_name

            if not dependency_file.is_file():
                continue

            if file_name == "requirements.txt":
                file_matches = self._read_requirements_matches(
                    dependency_file,
                    project_path,
                    normalized_name,
                )
            elif file_name in {
                "pyproject.toml",
                "Pipfile",
                "poetry.lock",
                "uv.lock",
            }:
                file_matches = self._read_toml_matches(
                    dependency_file,
                    project_path,
                    normalized_name,
                )
            elif file_name == "setup.cfg":
                file_matches = self._read_setup_cfg_matches(
                    dependency_file,
                    project_path,
                    normalized_name,
                )
            else:
                file_matches = self._read_setup_py_matches(
                    dependency_file,
                    project_path,
                    normalized_name,
                )

            matches.extend(file_matches)

        return list(dict.fromkeys(matches))

    def _read_requirements_matches(
        self,
        path: Path,
        project_path: Path,
        package_name: str,
    ) -> list[DependencyMatch]:
        try:
            lines = path.read_text(
                encoding="utf-8-sig"
            ).splitlines()
        except (OSError, UnicodeDecodeError):
            return []

        matches: list[DependencyMatch] = []

        for line_number, line in enumerate(lines, start=1):
            if self._requirement_package_name(line) != package_name:
                continue

            matches.append(
                DependencyMatch(
                    file_path=self._relative_path(path, project_path),
                    line_number=line_number,
                    declaration=line.strip(),
                )
            )

        return matches

    def _read_toml_matches(
        self,
        path: Path,
        project_path: Path,
        package_name: str,
    ) -> list[DependencyMatch]:
        try:
            source = path.read_text(encoding="utf-8-sig")
            data = tomllib.loads(source)
        except (
            OSError,
            UnicodeDecodeError,
            tomllib.TOMLDecodeError,
        ):
            return []

        declarations = self._toml_declarations(
            path.name,
            data,
            package_name,
        )
        line_number = self._find_package_line_number(
            source,
            package_name,
        )

        return [
            DependencyMatch(
                file_path=self._relative_path(path, project_path),
                line_number=line_number,
                declaration=declaration,
            )
            for declaration in declarations
        ]

    def _toml_declarations(
        self,
        file_name: str,
        data: dict[str, Any],
        package_name: str,
    ) -> list[str]:
        if file_name == "pyproject.toml":
            return self._pyproject_declarations(
                data,
                package_name,
            )

        if file_name == "Pipfile":
            packages = data.get("packages", {})

            if not isinstance(packages, dict):
                return []

            return [
                str(name)
                for name in packages
                if self._normalize_package_name(str(name))
                == package_name
            ]

        packages = data.get("package", [])

        if not isinstance(packages, list):
            return []

        return [
            str(package.get("name"))
            for package in packages
            if isinstance(package, dict)
            and self._normalize_package_name(
                str(package.get("name", ""))
            )
            == package_name
        ]

    def _pyproject_declarations(
        self,
        data: dict[str, Any],
        package_name: str,
    ) -> list[str]:
        declarations: list[str] = []

        project = data.get("project", {})

        if isinstance(project, dict):
            dependencies = project.get("dependencies", [])

            if isinstance(dependencies, list):
                declarations.extend(
                    str(value)
                    for value in dependencies
                    if self._requirement_package_name(str(value))
                    == package_name
                )

        tool = data.get("tool", {})

        if not isinstance(tool, dict):
            return declarations

        poetry = tool.get("poetry", {})

        if not isinstance(poetry, dict):
            return declarations

        poetry_dependencies = poetry.get("dependencies", {})

        if isinstance(poetry_dependencies, dict):
            declarations.extend(
                str(name)
                for name in poetry_dependencies
                if self._normalize_package_name(str(name))
                == package_name
            )

        return declarations

    def _read_setup_cfg_matches(
        self,
        path: Path,
        project_path: Path,
        package_name: str,
    ) -> list[DependencyMatch]:
        parser = configparser.ConfigParser(interpolation=None)

        try:
            parser.read(path, encoding="utf-8-sig")
            source = path.read_text(encoding="utf-8-sig")
        except (
            configparser.Error,
            OSError,
            UnicodeDecodeError,
        ):
            return []

        if not parser.has_option("options", "install_requires"):
            return []

        declarations = parser.get(
            "options",
            "install_requires",
        ).splitlines()
        line_number = self._find_package_line_number(
            source,
            package_name,
        )

        return [
            DependencyMatch(
                file_path=self._relative_path(path, project_path),
                line_number=line_number,
                declaration=declaration.strip(),
            )
            for declaration in declarations
            if self._requirement_package_name(declaration)
            == package_name
        ]

    def _read_setup_py_matches(
        self,
        path: Path,
        project_path: Path,
        package_name: str,
    ) -> list[DependencyMatch]:
        try:
            source = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            return []

        matches: list[DependencyMatch] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            for keyword in node.keywords:
                if keyword.arg != "install_requires":
                    continue

                for declaration in self._string_values(
                    keyword.value
                ):
                    if (
                        self._requirement_package_name(declaration)
                        != package_name
                    ):
                        continue

                    matches.append(
                        DependencyMatch(
                            file_path=self._relative_path(
                                path,
                                project_path,
                            ),
                            line_number=keyword.value.lineno,
                            declaration=declaration,
                        )
                    )

        return matches

    def _string_values(self, node: ast.AST) -> list[str]:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return [node.value]

            return []

        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return [
                value
                for element in node.elts
                for value in self._string_values(element)
            ]

        return []

    def _requirement_package_name(
        self,
        value: str,
    ) -> str | None:
        cleaned = value.strip()

        if not cleaned or cleaned.startswith(("#", "-", "--")):
            return None

        match = re.match(
            r"^([A-Za-z0-9][A-Za-z0-9._-]*)",
            cleaned,
        )

        if match is None:
            return None

        return self._normalize_package_name(match.group(1))

    def _validate_package_name(self, value: str) -> str:
        cleaned = value.strip()

        if re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]*",
            cleaned,
        ) is None:
            raise ValueError(
                "package_name must be a valid base package name"
            )

        return self._normalize_package_name(cleaned)

    def _normalize_package_name(self, value: str) -> str:
        return re.sub(r"[-_.]+", "-", value).lower()

    def _find_package_line_number(
        self,
        source: str,
        package_name: str,
    ) -> int | None:
        for line_number, line in enumerate(
            source.splitlines(),
            start=1,
        ):
            tokens = re.findall(
                r"[A-Za-z0-9][A-Za-z0-9._-]*",
                line,
            )

            if any(
                self._normalize_package_name(token)
                == package_name
                for token in tokens
            ):
                return line_number

        return None

    def _relative_path(
        self,
        path: Path,
        project_path: Path,
    ) -> str:
        try:
            return str(path.relative_to(project_path))
        except ValueError:
            return str(path)