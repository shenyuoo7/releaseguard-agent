"""Layer 4: Permission mode matrix evaluator."""

from releaseguard_agent.security.rules import Decision, PermissionMode


def decide_by_mode(
    mode: PermissionMode,
    tool_name: str,
    is_read_only: bool,
) -> Decision:
    """Evaluate permission based on the active PermissionMode matrix.

    Modes:
    - default: Allow read-only; Ask for file edits and Bash.
    - acceptEdits: Allow read-only and file edits; Ask for Bash.
    - plan: Allow read-only; Ask for file edits and Bash.
    - bypassPermissions: Allow everything (still subject to Layer 1 & 2 hard blocks).
    """
    if mode == PermissionMode.BYPASS_PERMISSIONS:
        return Decision.ALLOW

    if is_read_only:
        return Decision.ALLOW

    lower_name = tool_name.lower()
    is_bash = lower_name in ("bash", "sh")

    if mode == PermissionMode.ACCEPT_EDITS:
        if is_bash:
            return Decision.ASK
        return Decision.ALLOW

    if mode in (PermissionMode.DEFAULT, PermissionMode.PLAN):
        return Decision.ASK

    return Decision.ASK
