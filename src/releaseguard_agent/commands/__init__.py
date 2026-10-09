"""Built-in Slash Command framework for ReleaseGuard Agent (ch10).

Provides zero-token fast-path command interception, UI controls, and prompt injection.
"""

from releaseguard_agent.commands.builtin import build_default_command_registry
from releaseguard_agent.commands.models import (
    Command,
    CommandContext,
    CommandType,
    UIController,
)
from releaseguard_agent.commands.registry import CommandRegistry
from releaseguard_agent.commands.router import CommandRouter

__all__ = [
    "Command",
    "CommandContext",
    "CommandRegistry",
    "CommandRouter",
    "CommandType",
    "UIController",
    "build_default_command_registry",
]
