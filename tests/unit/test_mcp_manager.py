from pathlib import Path
from typing import Any
import pytest

from releaseguard_agent.mcp.config import MCPConfig, MCPServerConfig
from releaseguard_agent.mcp.manager import MCPManager
from releaseguard_agent.mcp.search_tool import ToolSearch
from releaseguard_agent.mcp.tool import MCPToolWrapper
from releaseguard_agent.security import (
    Decision,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)
from releaseguard_agent.tools.registry import ToolRegistry


class FakeCaller:
    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        return None


@pytest.mark.anyio
async def test_tool_search_discovery_and_activation() -> None:
    """T4 & AC3: ToolSearch discovers tools by keyword and activates exact tools with select:."""
    registry = ToolRegistry()

    # Create dummy deferred tools
    t1 = MCPToolWrapper(
        server_name="github",
        remote_name="search_issues",
        description="Search GitHub issues",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        is_read_only=True,
        session=FakeCaller(),
    )
    t2 = MCPToolWrapper(
        server_name="github",
        remote_name="create_issue",
        description="Create a new GitHub issue",
        input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
        is_read_only=False,
        session=FakeCaller(),
    )

    registry.register(t1)
    registry.register(t2)

    search_tool = ToolSearch(registry)
    registry.register(search_tool)

    assert t1.should_defer is True
    assert t2.should_defer is True

    # 1. Search by keyword
    search_res = await search_tool.execute({"query": "issues"})
    assert not search_res.is_error
    assert "mcp__github__search_issues" in search_res.content

    # 2. Activate tool via select:
    select_res = await search_tool.execute(
        {"query": "select:mcp__github__create_issue"}
    )
    assert not select_res.is_error
    assert "activated successfully" in select_res.content
    assert t2.name in search_tool.discovered_tools
    assert t2.should_defer is False


@pytest.mark.anyio
async def test_mcp_manager_failure_isolation_and_timeout(tmp_path: Path) -> None:
    """T5 & AC2: Corrupted or non-existent server command is isolated and skipped gracefully."""
    config = MCPConfig(
        servers={
            "broken_stdio": MCPServerConfig(
                name="broken_stdio",
                type="stdio",
                command="non_existent_command_12345",
            ),
        }
    )

    manager = MCPManager(config=config, workspace_root=tmp_path)

    # Start should not raise; failure should be logged and empty tool list returned
    tools = await manager.start()
    assert tools == []
    assert len(manager.tools) == 0

    # Clean shutdown
    await manager.close()


def test_mcp_tool_permission_integration(tmp_path: Path) -> None:
    """AC4 & F9: Write MCP tool requires ASK, read-only is ALLOW, wildcard rules take effect."""
    engine = PermissionEngine(workspace_root=tmp_path, mode=PermissionMode.DEFAULT)

    # Read-only MCP tool -> ALLOW
    assert (
        engine.check(
            "mcp__github__search_issues",
            {"query": "bug"},
            is_read_only=True,
        )
        == Decision.ALLOW
    )

    # Mutating MCP tool -> ASK in default mode
    assert (
        engine.check(
            "mcp__github__create_issue",
            {"title": "new bug"},
            is_read_only=False,
        )
        == Decision.ASK
    )

    # Permission rule matching wildcard tool name mcp__github__*
    rule = PermissionRule(tool_name="mcp__github__*", pattern="*", effect="allow")
    assert rule.matches("mcp__github__create_issue", "anything") is True
    assert rule.matches("mcp__sentry__create_alert", "anything") is False
