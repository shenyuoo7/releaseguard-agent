from pathlib import Path
from typing import Any

from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult


class ReadFileTool(BaseTool):
    """Tool for reading text files with line numbering, pagination, and binary defense."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read content from a file with line numbers. Supports offset and limit "
            "for pagination. Automatically guards against reading binary files."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read (relative to workspace or absolute).",
                },
                "offset": {
                    "type": "integer",
                    "description": "1-based starting line number to read from (default: 1).",
                    "default": 1,
                    "minimum": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default: 2000).",
                    "default": 2000,
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }

    @property
    def is_read_only(self) -> bool:
        return True

    def validate_input(self, arguments: dict[str, Any]) -> str | None:
        if "path" not in arguments or not isinstance(arguments["path"], str):
            return "Missing or invalid required argument 'path'."
        if not arguments["path"].strip():
            return "'path' must not be empty."
        if "offset" in arguments:
            if not isinstance(arguments["offset"], int) or arguments["offset"] < 1:
                return "'offset' must be a positive integer (>= 1)."
        if "limit" in arguments:
            if not isinstance(arguments["limit"], int) or arguments["limit"] < 1:
                return "'limit' must be a positive integer (>= 1)."
        return None

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        raw_path = arguments["path"].strip()
        offset = int(arguments.get("offset", 1))
        limit = int(arguments.get("limit", 2000))

        if context is not None:
            resolved_path = context.resolve_path(raw_path)
        else:
            resolved_path = Path(raw_path).resolve()

        if not resolved_path.exists():
            return ToolResult(
                content=f"Error: File not found: {raw_path}",
                is_error=True,
            )

        if not resolved_path.is_file():
            return ToolResult(
                content=f"Error: Path is a directory, not a file: {raw_path}",
                is_error=True,
            )

        # Binary check on first 512 bytes
        try:
            with open(resolved_path, "rb") as f:
                first_chunk = f.read(512)
                if b"\x00" in first_chunk:
                    return ToolResult(
                        content=f"Error: Cannot read binary file: {raw_path}",
                        is_error=True,
                    )
        except OSError as exc:
            return ToolResult(
                content=f"Error reading file '{raw_path}': {exc}",
                is_error=True,
            )

        # Read text content
        try:
            text = resolved_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(
                content=f"Error reading file '{raw_path}': {exc}",
                is_error=True,
            )

        lines = text.splitlines()
        total_lines = len(lines)
        start_index = offset - 1
        end_index = min(start_index + limit, total_lines)

        selected_lines = lines[start_index:end_index]

        output_rows = [f"{offset + i}\t{line}" for i, line in enumerate(selected_lines)]
        content_output = "\n".join(output_rows)

        return ToolResult(
            content=content_output,
            is_error=False,
            metadata={
                "path": str(resolved_path),
                "total_lines": total_lines,
                "offset": offset,
                "limit": limit,
                "lines_returned": len(selected_lines),
            },
        )
