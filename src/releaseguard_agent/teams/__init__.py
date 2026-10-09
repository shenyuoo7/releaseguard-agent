"""Autonomous multi-agent teams with task board and mailbox coordination."""

from releaseguard_agent.teams.backends import detect_backend
from releaseguard_agent.teams.board import TaskBoard
from releaseguard_agent.teams.coordinator import (
    COORDINATOR_MODE_ALLOWED_TOOLS,
    COORDINATOR_PROMPT,
    filter_tools_for_coordinator,
    is_coordinator_mode,
)
from releaseguard_agent.teams.mailbox import read_from_mailbox, write_to_mailbox
from releaseguard_agent.teams.models import AgentTeam, TeamTask, TeammateInfo
from releaseguard_agent.teams.manager import (
    SendMessageTool,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskUpdateTool,
    TeamCreateTool,
    TeamDeleteTool,
    TeamManager,
)

__all__ = [
    "COORDINATOR_MODE_ALLOWED_TOOLS",
    "COORDINATOR_PROMPT",
    "AgentTeam",
    "SendMessageTool",
    "TaskBoard",
    "TaskCreateTool",
    "TaskGetTool",
    "TaskListTool",
    "TeamCreateTool",
    "TeamDeleteTool",
    "TeamManager",
    "TeamTask",
    "TeammateInfo",
    "TaskUpdateTool",
    "detect_backend",
    "filter_tools_for_coordinator",
    "is_coordinator_mode",
    "read_from_mailbox",
    "write_to_mailbox",
]
