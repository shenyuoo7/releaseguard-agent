from pathlib import Path
from typing import Any

from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult


class WriteFileTool(BaseTool):
    """Tool for creating or overwriting files, automatically creating parent directories."""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Write full text content to a file. Overwrites existing files or creates new ones. "
            "Automatically creates any missing parent directories."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to write (relative to workspace or absolute).",
                },
                "content": {
                    "type": "string",
                    "description": "Full text content to write into the file.",
                },
            },
            "required": ["path", "content"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    def validate_input(self, arguments: dict[str, Any]) -> str | None:
        if "path" not in arguments or not isinstance(arguments["path"], str):
            return "Missing or invalid required argument 'path'."
        if not arguments["path"].strip():
            return "'path' must not be empty."
        if "content" not in arguments or not isinstance(arguments["content"], str):
            return "Missing or invalid required argument 'content'."
        return None

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        raw_path = arguments["path"].strip()
        content = arguments["content"]

        if context is not None:
            resolved_path = context.resolve_path(raw_path)
        else:
            resolved_path = Path(raw_path).resolve()

        try:
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            resolved_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(
                content=f"Error writing to file '{raw_path}': {exc}",
                is_error=True,
            )

        bytes_written = len(content.encode("utf-8"))
        return ToolResult(
            content=f"Successfully wrote {len(content)} characters ({bytes_written} bytes) to {raw_path}",
            is_error=False,
            metadata={
                "path": str(resolved_path),
                "characters_written": len(content),
                "bytes_written": bytes_written,
            },
        )
