"""Declarative lifecycle hook system for ReleaseGuard Agent."""

from releaseguard_agent.hooks.condition import evaluate_condition
from releaseguard_agent.hooks.engine import HookEngine
from releaseguard_agent.hooks.executors import (
    execute_command_action,
    execute_hook_action,
    execute_http_action,
    execute_prompt_action,
)
from releaseguard_agent.hooks.models import (
    HookAction,
    HookContext,
    HookDef,
    ToolRejectedError,
)

__all__ = [
    "HookAction",
    "HookContext",
    "HookDef",
    "HookEngine",
    "ToolRejectedError",
    "evaluate_condition",
    "execute_command_action",
    "execute_hook_action",
    "execute_http_action",
    "execute_prompt_action",
]
