from typing import Any

from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult


class ToolRegistry:
    """Central registry and dispatcher for all agent-callable tools."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance. Rejects duplicate tool names."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Retrieve a registered tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        """Return list of all registered tools."""
        return list(self._tools.values())

    def definitions(self) -> list[dict[str, Any]]:
        """Return generic tool definitions format (compatible with Anthropic input_schema)."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters_schema,
            }
            for t in self._tools.values()
        ]

    def openai_definitions(self) -> list[dict[str, Any]]:
        """Return tool definitions formatted for OpenAI Function Calling API."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters_schema,
                },
            }
            for t in self._tools.values()
        ]

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Validate and dispatch tool execution. Returns ToolResult."""
        tool = self.get(name)
        if not tool:
            return ToolResult(
                content=f"Error: Unknown tool '{name}'. Available tools: {', '.join(sorted(self._tools.keys()))}",
                is_error=True,
            )

        error_msg = tool.validate_input(arguments)
        if error_msg:
            return ToolResult(
                content=f"Invalid arguments for tool '{name}': {error_msg}",
                is_error=True,
            )

        try:
            return await tool.execute(arguments, context)
        except Exception as exc:
            return ToolResult(
                content=f"Error executing tool '{name}': {exc}",
                is_error=True,
            )
