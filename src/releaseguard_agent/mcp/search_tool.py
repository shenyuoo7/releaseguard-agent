"""ToolSearch lazy-loading discovery tool for deferred MCP tools."""

from typing import Any
import json

from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult
from releaseguard_agent.tools.registry import ToolRegistry


class ToolSearch(BaseTool):
    """Search and lazily discover full schemas for deferred tools.

    Solves context window token bloat when tens of MCP tools are available.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.discovered_tools: set[str] = set()

    @property
    def name(self) -> str:
        return "ToolSearch"

    @property
    def description(self) -> str:
        return (
            "Search available tools by keyword or activate an exact tool schema using "
            "'select:<tool_name>'. Use this to explore and expose detailed parameters for deferred tools."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search term (e.g. 'github issues') or exact selector (e.g. 'select:mcp__github__search_issues')."
                    ),
                },
            },
            "required": ["query"],
        }

    @property
    def is_read_only(self) -> bool:
        return True

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return ToolResult(
                content="Error: Query parameter must not be empty.",
                is_error=True,
            )

        # 1. Exact selection mode
        if query.lower().startswith("select:"):
            target_name = query[7:].strip()
            tool = self.registry.get(target_name)
            if not tool:
                return ToolResult(
                    content=f"Tool '{target_name}' not found in registry.",
                    is_error=True,
                )

            # Mark tool as discovered and disable deferred status
            self.discovered_tools.add(tool.name)
            if hasattr(tool, "should_defer"):
                setattr(tool, "should_defer", False)

            schema_json = json.dumps(
                tool.parameters_schema, indent=2, ensure_ascii=False
            )
            return ToolResult(
                content=(
                    f"Tool '{tool.name}' activated successfully.\n"
                    f"Description: {tool.description}\n"
                    f"Input Schema:\n{schema_json}\n\n"
                    f"You may now call '{tool.name}' directly with the above parameters."
                )
            )

        # 2. Keyword search mode
        q_lower = query.lower()
        matched: list[str] = []

        for t in self.registry.list_tools():
            # Skip searching the search tool itself
            if t.name == self.name:
                continue

            if q_lower in t.name.lower() or q_lower in t.description.lower():
                status = (
                    " (active)"
                    if not getattr(t, "should_defer", False)
                    else " (deferred)"
                )
                matched.append(f"- {t.name}{status}: {t.description}")

        if not matched:
            return ToolResult(
                content=f"No tools matched query '{query}'.",
            )

        return ToolResult(
            content=(
                f"Found {len(matched)} matching tools:\n"
                + "\n".join(matched)
                + "\n\nTo view parameters and activate a deferred tool, call ToolSearch(query='select:<tool_name>')."
            )
        )
