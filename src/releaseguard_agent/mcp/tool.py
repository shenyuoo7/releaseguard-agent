"""MCPToolWrapper adapting remote MCP tools to BaseTool."""

import asyncio
import re
import sys
from typing import Any, Protocol

from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult


class MCPCallerSession(Protocol):
    """Protocol for calling tools via an active MCP ClientSession."""

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any: ...


def sanitize_mcp_tool_name(server_name: str, remote_name: str) -> str:
    """Format and sanitize tool name to mcp__<server>__<tool> complying with schema constraints."""
    raw = f"mcp__{server_name}__{remote_name}"
    # Whitelist characters [a-zA-Z0-9_-]
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)
    # Truncate to maximum 64 characters if exceeded
    return sanitized[:64]


class MCPToolWrapper(BaseTool):
    """Adapter wrapping a remote MCP tool into a ReleaseGuard BaseTool."""

    def __init__(
        self,
        server_name: str,
        remote_name: str,
        description: str,
        input_schema: dict[str, Any] | None,
        is_read_only: bool,
        session: MCPCallerSession,
    ) -> None:
        self.server_name = server_name
        self.remote_name = remote_name
        self._name = sanitize_mcp_tool_name(server_name, remote_name)
        self._description = (
            description or f"MCP tool '{remote_name}' from server '{server_name}'"
        )
        self._parameters_schema = (
            input_schema
            if isinstance(input_schema, dict)
            else {
                "type": "object",
                "properties": {},
            }
        )
        self._is_read_only = bool(is_read_only)
        self.session = session
        self.should_defer = True  # Defer full schema loading via ToolSearch by default

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self._parameters_schema

    @property
    def is_read_only(self) -> bool:
        return self._is_read_only

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Dispatch tool invocation to remote MCP server with timeout and content extraction."""
        try:
            res = await asyncio.wait_for(
                self.session.call_tool(self.remote_name, arguments),
                timeout=30.0,
            )

            # Extract content blocks
            raw_blocks = getattr(res, "content", [])
            text_pieces: list[str] = []

            for block in raw_blocks:
                if hasattr(block, "text") and isinstance(block.text, str):
                    text_pieces.append(block.text)
                elif (
                    isinstance(block, dict)
                    and "text" in block
                    and isinstance(block["text"], str)
                ):
                    text_pieces.append(block["text"])
                else:
                    sys.stderr.write(
                        f"[ReleaseGuard MCP] Warning: Non-text content block received from tool '{self.name}'.\n"
                    )

            is_err = bool(
                getattr(res, "isError", False) or getattr(res, "is_error", False)
            )

            content = "\n".join(text_pieces) if text_pieces else ""
            return ToolResult(content=content, is_error=is_err)

        except asyncio.TimeoutError:
            return ToolResult(
                content=f"MCP tool call '{self.name}' timed out after 30 seconds.",
                is_error=True,
            )
        except Exception as e:
            return ToolResult(
                content=f"MCP tool call '{self.name}' failed: {e}",
                is_error=True,
            )
