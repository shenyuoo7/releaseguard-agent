"""Data models for the Skill package system."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class SkillMode(str, Enum):
    """Execution mode for skill activation."""

    INLINE = "inline"
    FORK = "fork"


class SkillContextMode(str, Enum):
    """Context passing strategy when executing in fork mode."""

    NONE = "none"
    RECENT = "recent"
    FULL = "full"


@dataclass(frozen=True)
class SkillDef:
    """Self-contained definition of an agent capability package."""

    name: str
    description: str
    prompt_body: str
    allowed_tools: tuple[str, ...] = ()
    model: str | None = None
    mode: SkillMode = SkillMode.INLINE
    context: SkillContextMode = SkillContextMode.FULL
    exclusive_tools: tuple[dict[str, Any], ...] = ()
    file_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Skill name must not be empty.")

    def render_prompt(self, arguments: str = "") -> str:
        """Render the SOP prompt body by substituting $ARGUMENTS."""
        rendered = self.prompt_body.replace("$ARGUMENTS", arguments.strip())
        return rendered.strip()
