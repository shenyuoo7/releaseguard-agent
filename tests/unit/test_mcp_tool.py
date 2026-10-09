import asyncio
from typing import Any
import pytest

from releaseguard_agent.mcp.tool import MCPToolWrapper, sanitize_mcp_tool_name


class DummyContent:
    def __init__(self, text: str) -> None:
        self.text = text


class DummyResult:
    def __init__(self, content: list[Any], is_error: bool = False) -> None:
        self.content = content
        self.isError = is_error


class StubSession:
    def __init__(
        self,
        result: Any = None,
        delay_s: float = 0.0,
        should_raise: Exception | None = None,
    ) -> None:
        self.result = result
        self.delay_s = delay_s
        self.should_raise = should_raise
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        self.calls.append((name, arguments))
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        if self.should_raise:
            raise self.should_raise
        return self.result


def test_sanitize_mcp_tool_name() -> None:
    """T3 & F6: Verify MCP tool naming format and length truncation."""
    name = sanitize_mcp_tool_name("github", "search_issues")
    assert name == "mcp__github__search_issues"

    # Invalid characters replaced
    name_weird = sanitize_mcp_tool_name("my server", "tool/with:symbols")
    assert name_weird == "mcp__my_server__tool_with_symbols"

    # Length truncation at 64 chars
    long_name = sanitize_mcp_tool_name("a" * 40, "b" * 40)
    assert len(long_name) <= 64


@pytest.mark.anyio
async def test_mcp_tool_wrapper_execution_success() -> None:
    """T3 & F6: Verify successful tool call text extraction."""
    session = StubSession(
        result=DummyResult(content=[DummyContent("line 1"), DummyContent("line 2")])
    )
    wrapper = MCPToolWrapper(
        server_name="git",
        remote_name="log",
        description="View git log",
        input_schema={"type": "object", "properties": {"max": {"type": "integer"}}},
        is_read_only=True,
        session=session,
    )

    assert wrapper.name == "mcp__git__log"
    assert wrapper.is_read_only is True
    assert wrapper.should_defer is True

    res = await wrapper.execute({"max": 5})
    assert res.is_error is False
    assert res.content == "line 1\nline 2"
    assert session.calls == [("log", {"max": 5})]


@pytest.mark.anyio
async def test_mcp_tool_wrapper_remote_error() -> None:
    """T3 & F6: Remote isError=True is translated to ToolResult.is_error=True."""
    session = StubSession(
        result=DummyResult(
            content=[DummyContent("Fatal: repo not found")], is_error=True
        )
    )
    wrapper = MCPToolWrapper(
        server_name="git",
        remote_name="checkout",
        description="Checkout branch",
        input_schema={},
        is_read_only=False,
        session=session,
    )

    res = await wrapper.execute({"branch": "missing"})
    assert res.is_error is True
    assert "repo not found" in res.content


@pytest.mark.anyio
async def test_mcp_tool_wrapper_exception_mapped() -> None:
    """T3: Exception during call is caught and formatted as structured ToolResult."""
    session = StubSession(should_raise=RuntimeError("Connection reset by peer"))
    wrapper = MCPToolWrapper(
        server_name="sentry",
        remote_name="get_issue",
        description="Fetch issue",
        input_schema={},
        is_read_only=True,
        session=session,
    )

    res = await wrapper.execute({"issue_id": 123})
    assert res.is_error is True
    assert "MCP tool call 'mcp__sentry__get_issue' failed" in res.content
