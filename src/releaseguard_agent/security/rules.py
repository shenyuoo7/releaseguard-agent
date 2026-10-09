"""Layer 3: Permission rules model, parsing, and three-layer cascaded evaluation."""

from dataclasses import dataclass
from enum import Enum
import fnmatch
from pathlib import Path
import re
from typing import Literal
import yaml


class Decision(Enum):
    ALLOW = "ALLOW"
    ASK = "ASK"
    DENY = "DENY"


class PermissionMode(Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS_PERMISSIONS = "bypassPermissions"


@dataclass(frozen=True)
class PermissionRule:
    """Fine-grained permission rule specifying tool, pattern, and effect."""

    tool_name: str
    pattern: str
    effect: Literal["allow", "deny"]
    source: str = ""

    def matches(self, tool_name: str, content: str) -> bool:
        """Check whether rule matches the given tool and argument content."""
        if self.tool_name != "*" and self.tool_name.lower() != tool_name.lower():
            return False
        # Empty pattern matches everything
        if not self.pattern or self.pattern == "*":
            return True
        return fnmatch.fnmatch(content, self.pattern)


def parse_rule_str(
    rule_str: str,
    effect: str = "allow",
    source: str = "",
) -> PermissionRule | None:
    """Parse a rule string such as 'Bash(git *)' or 'ReadFile(*.env*)'."""
    if not rule_str or not rule_str.strip():
        return None

    cleaned = rule_str.strip()
    match = re.match(r"^([a-zA-Z0-9_\*]+)(?:\((.*)\))?$", cleaned)
    if not match:
        return None

    tool_name = match.group(1).strip()
    pattern = (match.group(2) or "*").strip()

    normalized_effect: Literal["allow", "deny"] = (
        "allow" if effect.strip().lower() == "allow" else "deny"
    )

    return PermissionRule(
        tool_name=tool_name,
        pattern=pattern,
        effect=normalized_effect,
        source=source,
    )


def load_rules_from_file(config_path: Path, source_name: str) -> list[PermissionRule]:
    """Safely load permission rules from a YAML configuration file."""
    if not config_path.is_file():
        return []

    try:
        content = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            return []

        rules_list: list[PermissionRule] = []
        raw_rules = data.get("rules", [])
        if not isinstance(raw_rules, list):
            return []

        for item in raw_rules:
            if isinstance(item, dict):
                rule_str = item.get("rule", "")
                effect = item.get("effect", "allow")
                parsed = parse_rule_str(
                    str(rule_str), effect=str(effect), source=source_name
                )
                if parsed:
                    rules_list.append(parsed)
            elif isinstance(item, str):
                parsed = parse_rule_str(item, effect="allow", source=source_name)
                if parsed:
                    rules_list.append(parsed)

        return rules_list
    except Exception:
        # Fail-closed: invalid file yields no allow rules
        return []


def evaluate_cascaded_rules(
    tool_name: str,
    content: str,
    workspace_root: Path,
    user_home: Path | None = None,
) -> Decision | None:
    """Evaluate rules across Local, Project, and User layers.

    Two-pass scan algorithm:
    1. Scan ALL layers for 'deny' rules. If any deny matches, immediately return DENY.
       (Deny cannot be reversed by lower/higher layers).
    2. Scan 'allow' rules with priority: Local > Project > User.
       If any allow matches, return ALLOW.
    3. If neither matches, return None (fall back to permission mode matrix).
    """
    home = user_home or Path.home()
    user_cfg = home / ".releaseguard" / "permissions.yaml"
    project_cfg = workspace_root / ".releaseguard" / "permissions.yaml"
    local_cfg = workspace_root / ".releaseguard" / "permissions.local.yaml"

    local_rules = load_rules_from_file(local_cfg, "local")
    project_rules = load_rules_from_file(project_cfg, "project")
    user_rules = load_rules_from_file(user_cfg, "user")

    all_rules = [local_rules, project_rules, user_rules]

    # Pass 1: Global Deny check
    for layer in all_rules:
        for rule in layer:
            if rule.effect == "deny" and rule.matches(tool_name, content):
                return Decision.DENY

    # Pass 2: Cascaded Allow check (local > project > user)
    for layer in (local_rules, project_rules, user_rules):
        for rule in layer:
            if rule.effect == "allow" and rule.matches(tool_name, content):
                return Decision.ALLOW

    return None
