"""Coordinator Mode enforcement, tool restrictions, and 4-phase workflow prompt."""

import os

from releaseguard_agent.tools.registry import ToolRegistry


COORDINATOR_MODE_ALLOWED_TOOLS = frozenset(
    {
        "agent",
        "sendmessage",
        "send_message",
        "taskcreate",
        "task_create",
        "taskget",
        "task_get",
        "tasklist",
        "task_list",
        "taskupdate",
        "task_update",
        "teamcreate",
        "team_create",
        "teamdelete",
        "team_delete",
        "readfile",
        "read_file",
        "glob",
        "grep",
        "bash",
        "toolsearch",
        "tool_search",
    }
)

COORDINATOR_PROMPT = """
# Coordinator Mode Active
You are acting as the Lead Coordinator of an autonomous multi-agent team.
In this mode, you are intentionally stripped of direct file-writing tools (WriteFile, EditFile) to prevent resource conflicts.
You must guide the team through the four disciplined collaboration phases:

1. **Research (调研)**:
   - Spawn read-only exploration experts in isolated Worktrees to investigate issues.
   - Aggregate their findings via SendMessage.

2. **Synthesis (综合)**:
   - Personally draft a rigorous implementation specification based on teammate discoveries.
   - Do NOT delegate specification writing.

3. **Implementation (实施)**:
   - Create granular tasks on the TaskBoard, defining dependencies (blocked_by).
   - Direct teammates to implement code changes in their dedicated Worktrees.

4. **Verification & Merge (验证与收敛)**:
   - Delegate verification to gatekeeper agents.
   - When all tasks are completed, merge changes into the target branch via Git and request final user confirmation.
""".strip()


def is_coordinator_mode() -> bool:
    """Check if Coordinator Mode is explicitly enabled via environment or config."""
    return os.environ.get("RELEASEGUARD_COORDINATOR_MODE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _norm_tool_name(name: str) -> str:
    return name.lower().replace("_", "").replace("-", "")


def filter_tools_for_coordinator(registry: ToolRegistry) -> ToolRegistry:
    """Filter registry for Coordinator Lead, removing all direct write tools."""
    filtered = ToolRegistry()
    allowed_norm = {_norm_tool_name(t) for t in COORDINATOR_MODE_ALLOWED_TOOLS}

    for tool in registry.list_tools():
        if _norm_tool_name(tool.name) in allowed_norm:
            filtered.register(tool)

    return filtered
