"""Central 5-layer deep permission defense engine."""

from pathlib import Path
from typing import Any

from releaseguard_agent.security.blacklist import is_dangerous_command
from releaseguard_agent.security.mode import decide_by_mode
from releaseguard_agent.security.rules import (
    Decision,
    PermissionMode,
    evaluate_cascaded_rules,
)
from releaseguard_agent.security.sandbox import is_path_confined


class PermissionEngine:
    """Central arbiter evaluating tool executions through 5-layer defense.

    Layer 1: Dangerous shell command regex blacklist (Hard block)
    Layer 2: Path sandbox and symlink traversal defense (Hard block)
    Layer 3: Three-tier cascaded rule engine (Local > Project > User; Deny-override)
    Layer 4: PermissionMode matrix (default, acceptEdits, plan, bypassPermissions)
    Layer 5: Interactive Human-In-The-Loop confirmation & rule self-learning
    """

    def __init__(
        self,
        workspace_root: Path,
        mode: PermissionMode = PermissionMode.DEFAULT,
        allowed_roots: list[Path] | None = None,
        user_home: Path | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.mode = mode
        self.allowed_roots = allowed_roots or [self.workspace_root]
        self.user_home = user_home

    def _extract_content(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Extract the primary target string (command, path, pattern) for rule matching."""
        lower = tool_name.lower()
        if lower in ("bash", "sh"):
            return str(arguments.get("command", ""))
        if "path" in arguments:
            return str(arguments.get("path", ""))
        if "pattern" in arguments:
            return str(arguments.get("pattern", ""))
        for val in arguments.values():
            if isinstance(val, str):
                return val
        return ""

    def check(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        is_read_only: bool = False,
    ) -> Decision:
        """Evaluate permission for a proposed tool execution."""
        # Layer 1: Dangerous command blacklist (Hard Block)
        lower_name = tool_name.lower()
        if lower_name in ("bash", "sh"):
            cmd = arguments.get("command", "")
            if is_dangerous_command(cmd):
                return Decision.DENY

        # Layer 2: Path sandbox & symlink traversal (Hard Block)
        if "path" in arguments or lower_name in (
            "read_file",
            "write_file",
            "edit_file",
        ):
            target_path = arguments.get("path")
            if target_path and not is_path_confined(target_path, self.allowed_roots):
                return Decision.DENY

        # If bypassPermissions mode is active, skip soft policy rules (Layer 3-5)
        if self.mode == PermissionMode.BYPASS_PERMISSIONS:
            return Decision.ALLOW

        # Layer 3: Cascaded rules (Local > Project > User)
        content = self._extract_content(tool_name, arguments)
        rule_decision = evaluate_cascaded_rules(
            tool_name=tool_name,
            content=content,
            workspace_root=self.workspace_root,
            user_home=self.user_home,
        )
        if rule_decision is not None:
            return rule_decision

        # Layer 4: PermissionMode matrix
        return decide_by_mode(self.mode, tool_name, is_read_only)
