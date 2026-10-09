from typing import Any
import pytest

from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult
from releaseguard_agent.tools.registry import ToolRegistry


class DummyEchoTool(BaseTool):
    @property
    def name(self) -> str:
        return "echo_tool"

    @property
    def description(self) -> str:
        return "Echoes message back"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
            },
            "required": ["message"],
        }

    def validate_input(self, arguments: dict[str, Any]) -> str | None:
        if "message" not in arguments or not isinstance(arguments["message"], str):
            return "Missing 'message'"
        return None

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        return ToolResult(content=f"echo: {arguments['message']}")


class FailingTool(BaseTool):
    @property
    def name(self) -> str:
        return "failing_tool"

    @property
    def description(self) -> str:
        return "Always raises error"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object"}

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        raise RuntimeError("Something exploded")


@pytest.mark.anyio
async def test_tool_registry_registration_and_definitions() -> None:
    registry = ToolRegistry()
    tool = DummyEchoTool()
    registry.register(tool)

    # Duplicate registration raises ValueError
    with pytest.raises(ValueError, match="already registered"):
        registry.register(tool)

    assert registry.get("echo_tool") is tool
    assert registry.get("nonexistent") is None
    assert len(registry.list_tools()) == 1

    # Generic definitions
    defs = registry.definitions()
    assert len(defs) == 1
    assert defs[0]["name"] == "echo_tool"
    assert "input_schema" in defs[0]

    # OpenAI definitions
    openai_defs = registry.openai_definitions()
    assert len(openai_defs) == 1
    assert openai_defs[0]["type"] == "function"
    assert openai_defs[0]["function"]["name"] == "echo_tool"


@pytest.mark.anyio
async def test_tool_registry_execution_dispatch() -> None:
    registry = ToolRegistry()
    registry.register(DummyEchoTool())
    registry.register(FailingTool())

    # Success execution
    res = await registry.execute("echo_tool", {"message": "hello"})
    assert not res.is_error
    assert res.content == "echo: hello"

    block = res.to_tool_result_block("call_123")
    assert block.tool_use_id == "call_123"
    assert block.content == "echo: hello"
    assert not block.is_error

    # Validation failure
    val_res = await registry.execute("echo_tool", {})
    assert val_res.is_error
    assert "Invalid arguments" in val_res.content

    # Unknown tool
    unk_res = await registry.execute("mystery_tool", {})
    assert unk_res.is_error
    assert "Unknown tool" in unk_res.content

    # Unhandled exception inside tool
    fail_res = await registry.execute("failing_tool", {})
    assert fail_res.is_error
    assert "Something exploded" in fail_res.content
