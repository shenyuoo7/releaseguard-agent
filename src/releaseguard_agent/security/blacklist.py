"""Layer 1: Hard regex blacklist for dangerous shell commands."""

import re

DANGEROUS_COMMAND_PATTERNS = [
    # Recursive deletion of root, home, or wildcards
    re.compile(
        r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(/|/\*|~|~/\*|\$HOME|\$HOME/\*)(\s|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\brm\s+(-[a-zA-Z]*\s+)*(/\*?|~/\*?)(\s|$)",
        re.IGNORECASE,
    ),
    # Disk formatting
    re.compile(r"\bmkfs(\.[a-zA-Z0-9]+)?\s+", re.IGNORECASE),
    # Direct raw disk / device block writes
    re.compile(
        r"\bdd\s+.*of=/dev/(sd[a-z]|hd[a-z]|nvme[0-9]n[0-9]|vd[a-z]|loop)",
        re.IGNORECASE,
    ),
    # Recursive full permission removal or grant on root
    re.compile(
        r"\bchmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+)?(777|000)\s+(/|/\*)(\s|$)", re.IGNORECASE
    ),
    # Fork bomb
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.IGNORECASE),
    # Direct pipe to shell from downloaders
    re.compile(r"\b(curl|wget)\s+.*\|\s*(bash|sh|zsh|python|perl)\b", re.IGNORECASE),
    # Shutdown / reboot attempts
    re.compile(r"\b(shutdown|reboot|poweroff|init\s+[06])\b", re.IGNORECASE),
]


def is_dangerous_command(command: str) -> bool:
    """Check if command matches hard-blocked dangerous command patterns.

    Always returns True if any dangerous pattern is found.
    This check cannot be bypassed under any mode (even bypassPermissions).
    """
    if not command or not command.strip():
        return False

    cleaned = command.strip()

    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(cleaned):
            return True

    return False
