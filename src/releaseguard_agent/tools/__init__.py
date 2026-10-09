from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult
from releaseguard_agent.tools.bash import BashTool
from releaseguard_agent.tools.edit_file import EditFileTool
from releaseguard_agent.tools.glob_tool import GlobTool
from releaseguard_agent.tools.grep_tool import GrepTool
from releaseguard_agent.tools.read_file import ReadFileTool
from releaseguard_agent.tools.registry import ToolRegistry
from releaseguard_agent.tools.write_file import WriteFileTool


def build_default_tool_registry() -> ToolRegistry:
    """Build and return a ToolRegistry populated with the 6 core code tools."""
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(BashTool())
    registry.register(GlobTool())
    registry.register(GrepTool())
    return registry


__all__ = [
    "BaseTool",
    "BashTool",
    "EditFileTool",
    "GlobTool",
    "GrepTool",
    "ReadFileTool",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "WriteFileTool",
    "build_default_tool_registry",
]
