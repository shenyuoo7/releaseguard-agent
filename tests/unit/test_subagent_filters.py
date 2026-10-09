"""Unit tests for SubAgent 4-layer tool filtering and recursive spawn prevention."""

from typing import Any

from releaseguard_agent.subagent.filters import (
    resolve_subagent_tools,
)
from releaseguard_agent.subagent.loader import AgentLoader
from releaseguard_agent.subagent.models import AgentDefinition
from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult
from releaseguard_agent.tools.registry import ToolRegistry


class DummyTool(BaseTool):
    def __init__(self, tool_name: str, read_only: bool = True) -> None:
        self._name = tool_name
        self._ro = read_only

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Dummy {self._name}"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object"}

    @property
    def is_read_only(self) -> bool:
        return self._ro

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        return ToolResult(content=f"executed {self._name}")


def _build_test_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(DummyTool("Agent"))
    r.register(DummyTool("agent"))
    r.register(DummyTool("AskUserQuestion"))
    r.register(DummyTool("read_file"))
    r.register(DummyTool("write_file", read_only=False))
    r.register(DummyTool("bash", read_only=False))
    r.register(DummyTool("glob"))
    r.register(DummyTool("grep"))
    r.register(DummyTool("dangerous_custom_tool", read_only=False))
    return r


def test_layer1_anti_recursion_bans_agent_under_all_conditions() -> None:
    parent = _build_test_registry()
    assert parent.get("Agent") is not None

    # Even with an empty definition, Agent and AskUserQuestion must be physically purged
    sub_registry = resolve_subagent_tools(parent, definition=None, is_async=False)
    tool_names = {t.name for t in sub_registry.list_tools()}

    assert "Agent" not in tool_names
    assert "agent" not in tool_names
    assert "AskUserQuestion" not in tool_names
    assert "read_file" in tool_names


def test_layer2_async_background_strict_whitelist() -> None:
    parent = _build_test_registry()
    sub_registry = resolve_subagent_tools(parent, definition=None, is_async=True)
    tool_names = {t.name for t in sub_registry.list_tools()}

    # Only tools in ASYNC_AGENT_ALLOWED_TOOLS can survive
    assert "dangerous_custom_tool" not in tool_names
    assert "read_file" in tool_names
    assert "write_file" in tool_names
    assert "bash" in tool_names


def test_layer3_and_layer4_definition_whitelists_and_blacklists() -> None:
    parent = _build_test_registry()

    # Define an Explore-like agent: tools=[read_file, glob], disallowed=[write_file]
    definition = AgentDefinition(
        name="ExploreTest",
        description="test",
        system_prompt="explore",
        tools=("read_file", "glob", "write_file"),
        disallowed_tools=("write_file",),
    )

    sub_registry = resolve_subagent_tools(parent, definition=definition, is_async=False)
    tool_names = {t.name for t in sub_registry.list_tools()}

    assert "read_file" in tool_names
    assert "glob" in tool_names
    # write_file is in tools but disallowed by disallowed_tools
    assert "write_file" not in tool_names
    # bash is not in tools whitelist
    assert "bash" not in tool_names


def test_builtin_agents_loading_and_attributes() -> None:
    loader = AgentLoader()
    agents = loader.load_all()

    assert "explore" in agents
    assert "plan" in agents
    assert "verification" in agents
    assert "general-purpose" in agents

    explore = agents["explore"]
    assert explore.permission_mode == "dontAsk"
    assert "read_file" in explore.tools or "readfile" in [
        t.lower().replace("_", "") for t in explore.tools
    ]
    assert "write_file" in explore.disallowed_tools or "writefile" in [
        t.lower().replace("_", "") for t in explore.disallowed_tools
    ]
