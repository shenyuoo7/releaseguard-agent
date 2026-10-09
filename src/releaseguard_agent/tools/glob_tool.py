import fnmatch
from pathlib import Path
from typing import Any

from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult

EXCLUDED_DIRS = {
    ".git",
    ".runtime",
    "node_modules",
    "__pycache__",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
}


class GlobTool(BaseTool):
    """Tool for pattern-based file discovery while ignoring build and cache directories."""

    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return (
            "Search for files matching a glob pattern (e.g. '**/*.py' or 'tests/test_*.py'). "
            "Automatically filters out .git, .venv, node_modules, and cache directories. Capped at 200 results."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match file paths against.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory path to search in (defaults to workspace root).",
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
        if not arguments["pattern"].strip():
            return "'pattern' must not be empty."
        return None

    def _should_skip_dir(self, directory_name: str) -> bool:
        return directory_name in EXCLUDED_DIRS

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        pattern = arguments["pattern"].strip()
        search_root_arg = arguments.get("path")

        if context is not None:
            base_dir = (
                context.resolve_path(search_root_arg)
                if search_root_arg
                else context.cwd
            )
        else:
            base_dir = (
                Path(search_root_arg).resolve()
                if search_root_arg
                else Path.cwd().resolve()
            )

        if not base_dir.exists():
            return ToolResult(
                content=f"Error: Search directory not found: {base_dir}",
                is_error=True,
            )

        matches: list[Path] = []
        max_results = 200

        # Walk directory skipping excluded paths
        for p in base_dir.rglob("*"):
            # Check if any parent path component is in EXCLUDED_DIRS
            parts = p.relative_to(base_dir).parts
            if any(self._should_skip_dir(part) for part in parts[:-1]):
                continue

            if p.is_file():
                rel_path_str = str(p.relative_to(base_dir)).replace("\\", "/")
                if fnmatch.fnmatch(rel_path_str, pattern) or fnmatch.fnmatch(
                    p.name, pattern
                ):
                    matches.append(p)
                    if len(matches) >= max_results:
                        break

        # Sort matches by relative path
        formatted_list = [
            str(m.relative_to(base_dir)).replace("\\", "/") for m in matches
        ]
        formatted_list.sort()

        if not formatted_list:
            return ToolResult(
                content=f"No files matched pattern '{pattern}' in '{base_dir}'.",
                is_error=False,
                metadata={"count": 0, "pattern": pattern},
            )

        output_str = "\n".join(formatted_list)
        if len(matches) >= max_results:
            output_str += f"\n\n[Warning: Results capped at {max_results} files]"

        return ToolResult(
            content=output_str,
            is_error=False,
            metadata={"count": len(matches), "pattern": pattern},
        )
