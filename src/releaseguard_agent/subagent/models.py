"""Data models for SubAgent definitions and background tasks."""

from dataclasses import dataclass, field
import time
from typing import Literal


@dataclass(frozen=True)
class AgentDefinition:
    """Specification of a defined specialized expert subagent."""

    name: str
    description: str
    system_prompt: str
    tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    model: str | None = None
    max_turns: int = 30
    permission_mode: str = "default"  # "default" or "dontAsk"


@dataclass
class BackgroundTask:
    """Tracks asynchronous execution state of a background subagent task."""

    id: str
    name: str
    status: Literal["running", "completed", "failed"] = "running"
    result: str = ""
    error: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None

    def to_xml(self) -> str:
        """Render notification XML block for main conversation injection."""
        content = self.result if self.status == "completed" else self.error
        return (
            f"<task-notification>\n"
            f"  <task-id>{self.id}</task-id>\n"
            f"  <status>{self.status}</status>\n"
            f"  <summary>{self.name}</summary>\n"
            f"  <result>{content}</result>\n"
            f"</task-notification>"
        )
