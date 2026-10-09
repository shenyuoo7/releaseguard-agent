import fnmatch
import re
from pathlib import Path
from typing import Any

from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult
from releaseguard_agent.tools.glob_tool import EXCLUDED_DIRS


class GrepTool(BaseTool):
    """Tool for regular expression search across text files."""

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search for regular expression patterns in files. Returns file path, line number, "
            "and matched line content. Automatically ignores binary files and common cache directories."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression pattern to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory path to search within (defaults to workspace root).",
                },
                "include": {
                    "type": "string",
                    "description": "Filename pattern filter to narrow search (e.g. '*.py', '*.md').",
                },
            },
            "required": ["pattern"],
        }

    @property
    def is_read_only(self) -> bool:
        return True

    def validate_input(self, arguments: dict[str, Any]) -> str | None:
        if "pattern" not in arguments or not isinstance(arguments["pattern"], str):
            return "Missing or invalid required argument 'pattern'."
        if not arguments["pattern"]:
            return "'pattern' must not be empty."
        try:
            re.compile(arguments["pattern"])
        except re.error as exc:
            return f"Invalid regular expression pattern: {exc}"
        return None

    def _is_binary(self, file_path: Path) -> bool:
        try:
            with open(file_path, "rb") as f:
                return b"\x00" in f.read(512)
        except OSError:
            return True

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        pattern_str = arguments["pattern"]
        search_path_arg = arguments.get("path")
        include_pattern = arguments.get("include")

        if context is not None:
            base_path = (
                context.resolve_path(search_path_arg)
                if search_path_arg
                else context.cwd
            )
        else:
            base_path = (
                Path(search_path_arg).resolve()
                if search_path_arg
                else Path.cwd().resolve()
            )

        if not base_path.exists():
            return ToolResult(
                content=f"Error: Search path not found: {base_path}",
                is_error=True,
            )

        regex = re.compile(pattern_str)
        results: list[str] = []
        max_matches = 100

        files_to_scan: list[Path] = []
        if base_path.is_file():
            files_to_scan.append(base_path)
            root_dir = base_path.parent
        else:
            root_dir = base_path
            for p in base_path.rglob("*"):
                parts = p.relative_to(base_path).parts
                if any(part in EXCLUDED_DIRS for part in parts[:-1]):
                    continue
                if p.is_file():
                    if include_pattern and not fnmatch.fnmatch(p.name, include_pattern):
                        continue
                    files_to_scan.append(p)

        for file_path in files_to_scan:
            if self._is_binary(file_path):
                continue

            try:
                rel_display = str(file_path.relative_to(root_dir)).replace("\\", "/")
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, start=1):
                        if regex.search(line):
                            clean_line = line.rstrip("\r\n")
                            results.append(f"{rel_display}:{line_num}: {clean_line}")
                            if len(results) >= max_matches:
                                break
            except OSError:
                continue

            if len(results) >= max_matches:
                break

        if not results:
            return ToolResult(
                content=f"No matches found for regex pattern '{pattern_str}'.",
                is_error=False,
                metadata={"matches": 0, "pattern": pattern_str},
            )

        output_str = "\n".join(results)
        if len(results) >= max_matches:
            output_str += f"\n\n[Warning: Results capped at {max_matches} matches]"

        return ToolResult(
            content=output_str,
            is_error=False,
            metadata={"matches": len(results), "pattern": pattern_str},
        )
