"""Security package exporting 5-layer permission defense and HITL components."""

from releaseguard_agent.security.blacklist import is_dangerous_command
from releaseguard_agent.security.engine import PermissionEngine
from releaseguard_agent.security.hitl import (
    FutureHITLBridge,
    HITLChoice,
    append_local_allow_rule,
)
from releaseguard_agent.security.mode import decide_by_mode
from releaseguard_agent.security.rules import (
    Decision,
    PermissionMode,
    PermissionRule,
    evaluate_cascaded_rules,
    parse_rule_str,
)
from releaseguard_agent.security.sandbox import is_path_confined

__all__ = [
    "Decision",
    "FutureHITLBridge",
    "HITLChoice",
    "PermissionEngine",
    "PermissionMode",
    "PermissionRule",
    "append_local_allow_rule",
    "decide_by_mode",
    "evaluate_cascaded_rules",
    "is_dangerous_command",
    "is_path_confined",
    "parse_rule_str",
]
