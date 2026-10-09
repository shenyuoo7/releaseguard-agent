"""Data models for AgentTeam, Teammates, and shared TeamTask board."""

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import time
from typing import Any, Literal


@dataclass
class TeammateInfo:
    """Metadata describing a member in an AgentTeam."""

    name: str
    agent_id: str
    agent_type: str
    model: str | None = None
    worktree_path: str = ""
    backend_type: Literal["tmux", "iterm2", "in-process"] = "in-process"
    is_active: bool = True
    plan_mode_required: bool = False


@dataclass
class TeamTask:
    """A collaborative unit of work on the shared team board."""

    id: str
    subject: str
    description: str = ""
    status: Literal["pending", "in_progress", "completed"] = "pending"
    owner: str = ""
    blocked_by: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class AgentTeam:
    """An autonomous multi-agent team with coordinator lead and teammates."""

    name: str
    lead_agent_id: str
    members: list[TeammateInfo] = field(default_factory=list)
    config_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert team object into a JSON-serializable dictionary."""
        return {
            "name": self.name,
            "lead_agent_id": self.lead_agent_id,
            "config_path": self.config_path,
            "members": [asdict(m) for m in self.members],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentTeam":
        """Reconstruct team instance from dictionary."""
        members = [
            TeammateInfo(
                name=m["name"],
                agent_id=m["agent_id"],
                agent_type=m["agent_type"],
                model=m.get("model"),
                worktree_path=m.get("worktree_path", ""),
                backend_type=m.get("backend_type", "in-process"),
                is_active=bool(m.get("is_active", True)),
                plan_mode_required=bool(m.get("plan_mode_required", False)),
            )
            for m in data.get("members", [])
        ]
        return cls(
            name=data["name"],
            lead_agent_id=data["lead_agent_id"],
            members=members,
            config_path=data.get("config_path", ""),
        )

    def save(self, target_path: Path | None = None) -> None:
        """Persist team metadata to team.json."""
        p = target_path or Path(self.config_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
