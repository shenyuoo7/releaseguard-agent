from pathlib import Path
from typing import Any

from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult


class EditFileTool(BaseTool):
    """Tool for unique in-place text replacement in a file."""

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Replace exact text in a file. Must only be used after reading the target file "
            "with read_file to confirm latest contents. The target 'old_string' must occur "
            "exactly once in the file to prevent ambiguous edits. Do not use bash for text edits."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit (relative to workspace or absolute).",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to be replaced. Must appear exactly once in the file.",
                },
                "new_string": {
                    "type": "string",
                    "description": "New replacement text to substitute in place of old_string.",
                },
            },
            "required": ["path", "old_string", "new_string"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    def validate_input(self, arguments: dict[str, Any]) -> str | None:
        if "path" not in arguments or not isinstance(arguments["path"], str):
            return "Missing or invalid required argument 'path'."
        if not arguments["path"].strip():
            return "'path' must not be empty."
        if "old_string" not in arguments or not isinstance(
            arguments["old_string"], str
        ):
            return "Missing or invalid required argument 'old_string'."
        if not arguments["old_string"]:
            return "'old_string' must not be empty."
        if "new_string" not in arguments or not isinstance(
            arguments["new_string"], str
        ):
            return "Missing or invalid required argument 'new_string'."
        return None

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        raw_path = arguments["path"].strip()
        old_string = arguments["old_string"]
        new_string = arguments["new_string"]

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

        try:
            content = resolved_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(
                content=f"Error reading file '{raw_path}': {exc}",
                is_error=True,
            )

        matches = content.count(old_string)
        if matches == 0:
            return ToolResult(
                content=(
                    f"Error: Target text ('old_string') was not found in '{raw_path}'. "
                    "Please read the file to verify its current contents before editing."
                ),
                is_error=True,
                metadata={"matches": 0},
            )

        if matches > 1:
            return ToolResult(
                content=(
                    f"Error: Target text ('old_string') matches {matches} times in '{raw_path}'. "
                    "Replacement must be uniquely identifiable. Please include more surrounding context lines."
                ),
                is_error=True,
                metadata={"matches": matches},
            )

        # Exactly 1 match
        new_content = content.replace(old_string, new_string, 1)

        try:
            resolved_path.write_text(new_content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(
                content=f"Error saving edited file '{raw_path}': {exc}",
                is_error=True,
            )

        return ToolResult(
            content=f"Successfully replaced 1 occurrence of target string in '{raw_path}'.",
            is_error=False,
            metadata={
                "path": str(resolved_path),
                "matches": 1,
                "old_len": len(old_string),
                "new_len": len(new_string),
            },
        )
