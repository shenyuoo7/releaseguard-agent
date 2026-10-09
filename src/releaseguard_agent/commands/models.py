"""Data models and UI protocol for the Slash Command framework."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol


class CommandType(str, Enum):
    """Categorization of slash commands determining execution strategy."""

    LOCAL = "local"
    LOCAL_UI = "local-ui"
    PROMPT = "prompt"


class UIController(Protocol):
    """Standardized interface for commands to interact with the host UI."""

    def add_system_message(self, text: str) -> None:
        """Display an informational or error message from the system."""
        ...

    def send_user_message(self, text: str) -> None:
        """Inject a synthetic user message directly into the agent dialogue loop."""
        ...

    def set_plan_mode(self, enabled: bool) -> None:
        """Toggle between read-only planning mode and execution mode."""
        ...

    def get_token_count(self) -> int:
        """Retrieve the current estimated token count of the active conversation."""
        ...

    def refresh_status(self) -> None:
        """Request the host UI to re-render its status indicators."""
        ...

    def clear_chat(self) -> None:
        """Clear the visual conversation history."""
        ...


@dataclass
class CommandContext:
    """Dependency bag providing runtime context and services to command handlers."""

    args: str = ""
    agent: Any = None
    conversation: Any = None
    session: Any = None
    ui: UIController | None = None
    config: Any = None
    workspace_root: Path = field(default_factory=Path.cwd)


@dataclass(frozen=True)
class Command:
    """Metadata and execution handler for a registered slash command."""

    name: str
    description: str
    handler: Callable[[CommandContext], Any]
    aliases: tuple[str, ...] = ()
    usage: str = ""
    command_type: CommandType = CommandType.LOCAL
    arg_prompt: str = ""
    hidden: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.name.isalnum():
            # Allow lowercase letters and dashes/underscores
            cleaned = self.name.replace("-", "").replace("_", "")
            if not cleaned.isalnum():
                raise ValueError(f"Command name '{self.name}' must be alphanumeric.")
