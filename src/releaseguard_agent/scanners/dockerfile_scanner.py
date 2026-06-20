import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DockerParserDirective:
    """One Dockerfile parser directive."""

    key: str
    value: str
    line_number: int
    source_line: str

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "value": self.value,
            "line_number": self.line_number,
            "source_line": self.source_line,
        }


@dataclass(frozen=True)
class DockerInstruction:
    """One parsed Dockerfile instruction."""

    keyword: str
    original_keyword: str
    arguments: str
    start_line: int
    end_line: int
    raw_text: str

    def to_dict(self) -> dict[str, object]:
        return {
            "keyword": self.keyword,
            "original_keyword": self.original_keyword,
            "arguments": self.arguments,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "raw_text": self.raw_text,
        }


@dataclass(frozen=True)
class DockerParseIssue:
    """A nonfatal problem found while parsing a Dockerfile."""

    line_number: int
    message: str
    source_line: str

    def to_dict(self) -> dict[str, object]:
        return {
            "line_number": self.line_number,
            "message": self.message,
            "source_line": self.source_line,
        }


@dataclass(frozen=True)
class DockerfileScan:
    """Structured result produced by DockerfileScanner."""

    file_path: str
    exists: bool
    escape_character: str
    parser_directives: tuple[DockerParserDirective, ...]
    instructions: tuple[DockerInstruction, ...]
    issues: tuple[DockerParseIssue, ...]
    read_error: str | None = None

    @property
    def readable(self) -> bool:
        return self.exists and self.read_error is None

    @property
    def has_issues(self) -> bool:
        return bool(self.issues or self.read_error)

    def find_instructions(
        self,
        keyword: str,
    ) -> tuple[DockerInstruction, ...]:
        normalized_keyword = keyword.strip().upper()

        if not normalized_keyword:
            raise ValueError("keyword must not be empty")

        return tuple(
            instruction
            for instruction in self.instructions
            if instruction.keyword == normalized_keyword
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "exists": self.exists,
            "readable": self.readable,
            "escape_character": self.escape_character,
            "parser_directives": [
                directive.to_dict()
                for directive in self.parser_directives
            ],
            "instructions": [
                instruction.to_dict()
                for instruction in self.instructions
            ],
            "issues": [
                issue.to_dict()
                for issue in self.issues
            ],
            "read_error": self.read_error,
        }


class DockerfileScanner:
    """Parse a root Dockerfile into reusable structured evidence."""

    dockerfile_name = "Dockerfile"
    default_escape_character = "\\"

    supported_parser_directives = frozenset(
        {
            "syntax",
            "escape",
            "check",
        }
    )

    parser_directive_pattern = re.compile(
        r"^\s*#\s*"
        r"([A-Za-z][A-Za-z0-9_-]*)"
        r"\s*=\s*(.*?)\s*$"
    )

    instruction_pattern = re.compile(
        r"^\s*([A-Za-z]+)"
        r"(?:[ \t]+(.*?))?\s*$"
    )

    def scan(self, project_path: Path) -> DockerfileScan:
        """Scan the root-level Dockerfile in a project."""

        project_path = Path(project_path)
        dockerfile_path = project_path / self.dockerfile_name

        if not dockerfile_path.is_file():
            return DockerfileScan(
                file_path=self.dockerfile_name,
                exists=False,
                escape_character=self.default_escape_character,
                parser_directives=(),
                instructions=(),
                issues=(),
            )

        try:
            source = dockerfile_path.read_text(
                encoding="utf-8-sig"
            )
        except (OSError, UnicodeDecodeError) as error:
            return DockerfileScan(
                file_path=self.dockerfile_name,
                exists=True,
                escape_character=self.default_escape_character,
                parser_directives=(),
                instructions=(),
                issues=(),
                read_error=(
                    f"{type(error).__name__}: {error}"
                ),
            )

        return self.scan_text(
            source,
            file_path=self.dockerfile_name,
        )

    def scan_text(
        self,
        source: str,
        *,
        file_path: str = "Dockerfile",
    ) -> DockerfileScan:
        """Parse Dockerfile source already loaded in memory."""

        lines = source.splitlines()

        (
            parser_directives,
            escape_character,
            directive_issues,
        ) = self._read_parser_directives(lines)

        instructions, instruction_issues = (
            self._read_instructions(
                lines,
                escape_character,
            )
        )

        return DockerfileScan(
            file_path=file_path,
            exists=True,
            escape_character=escape_character,
            parser_directives=tuple(parser_directives),
            instructions=tuple(instructions),
            issues=tuple(
                directive_issues + instruction_issues
            ),
        )

    def _read_parser_directives(
        self,
        lines: list[str],
    ) -> tuple[
        list[DockerParserDirective],
        str,
        list[DockerParseIssue],
    ]:
        directives: list[DockerParserDirective] = []
        issues: list[DockerParseIssue] = []
        seen_keys: set[str] = set()
        escape_character = self.default_escape_character

        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()

            if not stripped:
                break

            if not stripped.startswith("#"):
                break

            match = self.parser_directive_pattern.fullmatch(
                line
            )

            if match is None:
                break

            key = match.group(1).lower()
            value = match.group(2).strip()

            if key not in self.supported_parser_directives:
                break

            directives.append(
                DockerParserDirective(
                    key=key,
                    value=value,
                    line_number=line_number,
                    source_line=line,
                )
            )

            if key in seen_keys:
                issues.append(
                    DockerParseIssue(
                        line_number=line_number,
                        message=(
                            "Duplicate Docker parser directive: "
                            f"{key}"
                        ),
                        source_line=line,
                    )
                )
                continue

            seen_keys.add(key)

            if key != "escape":
                continue

            if value not in {"\\", "`"}:
                issues.append(
                    DockerParseIssue(
                        line_number=line_number,
                        message=(
                            "Unsupported Docker escape "
                            f"character: {value}"
                        ),
                        source_line=line,
                    )
                )
                continue

            escape_character = value

        return directives, escape_character, issues

    def _read_instructions(
        self,
        lines: list[str],
        escape_character: str,
    ) -> tuple[
        list[DockerInstruction],
        list[DockerParseIssue],
    ]:
        instructions: list[DockerInstruction] = []
        issues: list[DockerParseIssue] = []
        index = 0

        while index < len(lines):
            line = lines[index]
            stripped = line.strip()

            if not stripped or stripped.startswith("#"):
                index += 1
                continue

            start_line = index + 1
            raw_lines = [line]
            unterminated = False

            while self._has_line_continuation(
                raw_lines[-1],
                escape_character,
            ):
                if index + 1 >= len(lines):
                    unterminated = True
                    break

                index += 1
                raw_lines.append(lines[index])

            if unterminated:
                issues.append(
                    DockerParseIssue(
                        line_number=start_line,
                        message=(
                            "Docker instruction has an "
                            "unterminated line continuation."
                        ),
                        source_line=raw_lines[-1],
                    )
                )

            instruction = self._build_instruction(
                raw_lines,
                start_line,
                escape_character,
            )

            if instruction is None:
                issues.append(
                    DockerParseIssue(
                        line_number=start_line,
                        message=(
                            "Line is not a recognizable "
                            "Dockerfile instruction."
                        ),
                        source_line=raw_lines[0],
                    )
                )
            else:
                instructions.append(instruction)

            index += 1

        return instructions, issues

    def _build_instruction(
        self,
        raw_lines: list[str],
        start_line: int,
        escape_character: str,
    ) -> DockerInstruction | None:
        first_line = self._remove_line_continuation(
            raw_lines[0],
            escape_character,
        )

        match = self.instruction_pattern.fullmatch(
            first_line
        )

        if match is None:
            return None

        original_keyword = match.group(1)
        first_arguments = (match.group(2) or "").strip()
        argument_parts: list[str] = []

        if first_arguments:
            argument_parts.append(first_arguments)

        for line in raw_lines[1:]:
            value = self._remove_line_continuation(
                line,
                escape_character,
            ).strip()

            if not value or value.startswith("#"):
                continue

            argument_parts.append(value)

        return DockerInstruction(
            keyword=original_keyword.upper(),
            original_keyword=original_keyword,
            arguments=" ".join(argument_parts),
            start_line=start_line,
            end_line=start_line + len(raw_lines) - 1,
            raw_text="\n".join(raw_lines),
        )

    def _has_line_continuation(
        self,
        line: str,
        escape_character: str,
    ) -> bool:
        stripped = line.rstrip()

        if not stripped:
            return False

        trailing_count = 0

        for character in reversed(stripped):
            if character != escape_character:
                break

            trailing_count += 1

        return trailing_count % 2 == 1

    def _remove_line_continuation(
        self,
        line: str,
        escape_character: str,
    ) -> str:
        stripped = line.rstrip()

        if self._has_line_continuation(
            stripped,
            escape_character,
        ):
            return stripped[:-1].rstrip()

        return stripped
