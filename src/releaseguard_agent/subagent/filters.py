"""Multi-layer tool filtering to prevent recursive subagent spawning and enforce safety boundaries."""

from releaseguard_agent.subagent.models import AgentDefinition
from releaseguard_agent.tools.registry import ToolRegistry


ALL_AGENT_DISALLOWED_TOOLS = frozenset({"agent", "askuserquestion", "taskstop"})

ASYNC_AGENT_ALLOWED_TOOLS = frozenset(
    {
        "readfile",
        "writefile",
        "editfile",
        "bash",
        "glob",
        "grep",
        "toolsearch",
        "loadskill",
        "enterworktree",
        "exitworktree",
    }
)


def _norm_name(name: str) -> str:
    """Normalize tool name to lowercase alphanumeric for robust matching."""
    return name.lower().replace("_", "").replace("-", "")


def resolve_subagent_tools(
    parent_registry: ToolRegistry,
    definition: AgentDefinition | None = None,
    is_async: bool = False,
) -> ToolRegistry:
    """Filter parent registry tools through a 4-layer defense line for a subagent."""
    filtered_registry = ToolRegistry()

    disallowed_set = {_norm_name(t) for t in ALL_AGENT_DISALLOWED_TOOLS}
    async_allowed_set = {_norm_name(t) for t in ASYNC_AGENT_ALLOWED_TOOLS}

    def_tools_set: set[str] | None = None
    if definition and definition.tools:
        def_tools_set = {_norm_name(t) for t in definition.tools}

    def_disallowed_set: set[str] = set()
    if definition and definition.disallowed_tools:
        def_disallowed_set = {_norm_name(t) for t in definition.disallowed_tools}

    for tool in parent_registry.list_tools():
        norm = _norm_name(tool.name)

        # Layer 1: Global anti-recursion ban (NEVER allow Agent in any subagent)
        if norm in disallowed_set:
            continue

        # Layer 2: Async background strict whitelist
        if is_async and norm not in async_allowed_set:
            continue

        # Layer 3: Definition-level disallowed list
        if norm in def_disallowed_set:
            continue

        # Layer 4: Definition-level allowed list whitelist (if specified)
        if def_tools_set is not None and norm not in def_tools_set:
            continue

        filtered_registry.register(tool)

    return filtered_registry
