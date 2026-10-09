import asyncio
from pathlib import Path
from typing import Any

from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult


class BashTool(BaseTool):
    """Tool for running shell commands with explicit cwd, timeout, and output truncation."""

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command within the workspace directory. "
            "Includes automatic timeout enforcement (default: 30s) and tail-biased output truncation."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command line to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Execution timeout in seconds (default: 30).",
                    "default": 30,
                    "minimum": 1,
                },
            },
            "required": ["command"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    def validate_input(self, arguments: dict[str, Any]) -> str | None:
        if "command" not in arguments or not isinstance(arguments["command"], str):
            return "Missing or invalid required argument 'command'."
        if not arguments["command"].strip():
            return "'command' must not be empty."
        if "timeout" in arguments:
            if not isinstance(arguments["timeout"], int) or arguments["timeout"] < 1:
                return "'timeout' must be a positive integer (>= 1)."
        return None

    def _truncate_output(self, text: str, max_chars: int = 10000) -> str:
        if len(text) <= max_chars:
            return text

        head_len = 2000
        tail_len = 8000
        truncated_count = len(text) - (head_len + tail_len)
        return (
            text[:head_len]
            + f"\n\n[... {truncated_count} characters truncated ...]\n\n"
            + text[-tail_len:]
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        command = arguments["command"].strip()
        timeout = int(arguments.get("timeout", 30))
        cwd = context.cwd if context is not None else Path.cwd()

        try:
            # Use PowerShell or default system shell
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )
        except OSError as exc:
            return ToolResult(
                content=f"Failed to start process: {exc}",
                is_error=True,
            )

        try:
            stdout_data, stderr_data = await asyncio.wait_for(
                process.communicate(),
                timeout=float(timeout),
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass
            return ToolResult(
                content=f"Error: Command timed out after {timeout} seconds: '{command}'",
                is_error=True,
                metadata={"command": command, "timeout": timeout},
            )

        stdout_str = stdout_data.decode("utf-8", errors="replace")
        stderr_str = stderr_data.decode("utf-8", errors="replace")

        combined_output = (
            stdout_str + ("\n" + stderr_str if stderr_str else "")
        ).strip()
        truncated_output = self._truncate_output(combined_output)

        formatted_result = (
            f"<output>\n{truncated_output}\n</output>\n"
            f"<exit_code>{process.returncode}</exit_code>"
        )

        return ToolResult(
            content=formatted_result,
            is_error=False,
            metadata={
                "command": command,
                "exit_code": process.returncode,
                "cwd": str(cwd),
            },
        )
