"""TeamManager orchestrating team lifecycles, boards, mailboxes, and agent tools."""

import json
from pathlib import Path
import shutil
import time
from typing import Any

from releaseguard_agent.teams.backends import detect_backend
from releaseguard_agent.teams.board import TaskBoard
from releaseguard_agent.teams.mailbox import read_from_mailbox, write_to_mailbox
from releaseguard_agent.teams.models import AgentTeam, TeammateInfo
from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult


class TeamManager:
    """Central manager for multi-agent teams, persistent boards, and communication mailboxes."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = (
            base_dir if base_dir else (Path.home() / ".releaseguard" / "teams")
        )
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _team_dir(self, team_name: str) -> Path:
        clean = team_name.strip().replace("..", "").replace("/", "_").replace("\\", "_")
        return self.base_dir / clean

    def create_team(
        self,
        team_name: str,
        lead_agent_id: str = "lead",
    ) -> AgentTeam:
        """Create new team workspace with team.json, task board, and mailbox directory."""
        tdir = self._team_dir(team_name)
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "mailbox").mkdir(parents=True, exist_ok=True)

        backend = detect_backend()
        lead_info = TeammateInfo(
            name="lead",
            agent_id=lead_agent_id,
            agent_type="coordinator",
            backend_type=backend,
            is_active=True,
        )

        team_file = tdir / "team.json"
        team = AgentTeam(
            name=team_name,
            lead_agent_id=lead_agent_id,
            members=[lead_info],
            config_path=str(team_file),
        )
        team.save()

        # Initialize empty board
        board = TaskBoard(board_file=tdir / "board.json")
        board.save()

        return team

    def get_team(self, team_name: str) -> AgentTeam | None:
        """Load an existing team by name."""
        tdir = self._team_dir(team_name)
        team_file = tdir / "team.json"
        if not team_file.is_file():
            return None
        try:
            data = json.loads(team_file.read_text(encoding="utf-8"))
            return AgentTeam.from_dict(data)
        except Exception:
            return None

    def delete_team(self, team_name: str) -> bool:
        """Delete team workspace and clean all resources."""
        tdir = self._team_dir(team_name)
        if not tdir.exists():
            return False
        shutil.rmtree(tdir, ignore_errors=True)
        return True

    def get_board(self, team_name: str) -> TaskBoard:
        """Get the shared task board for a team."""
        tdir = self._team_dir(team_name)
        return TaskBoard(board_file=tdir / "board.json")

    def send_message(
        self,
        team_name: str,
        sender_id: str,
        to: str,
        summary: str,
        message: str,
    ) -> bool:
        """Deliver a message to target agent's mailbox or broadcast to all teammates."""
        tdir = self._team_dir(team_name)
        mb_dir = tdir / "mailbox"
        if not mb_dir.is_dir():
            return False

        payload = {
            "from": sender_id,
            "to": to,
            "summary": summary,
            "message": message,
            "timestamp": time.time(),
        }

        if to == "*":
            team = self.get_team(team_name)
            if not team:
                return False
            success = True
            for member in team.members:
                if member.agent_id != sender_id:
                    ok = write_to_mailbox(mb_dir, member.agent_id, payload)
                    success = success and ok
            return success
        else:
            return write_to_mailbox(mb_dir, to, payload)

    def read_messages(
        self,
        team_name: str,
        agent_id: str,
        clear: bool = True,
    ) -> list[dict[str, Any]]:
        """Read pending messages for agent from their mailbox."""
        tdir = self._team_dir(team_name)
        mb_dir = tdir / "mailbox"
        return read_from_mailbox(mb_dir, agent_id, clear=clear)


# ---------------------------------------------------------------------------
# Coordinator Tools
# ---------------------------------------------------------------------------


class TeamCreateTool(BaseTool):
    def __init__(self, mgr: TeamManager) -> None:
        self.mgr = mgr

    @property
    def name(self) -> str:
        return "TeamCreate"

    @property
    def description(self) -> str:
        return "Create a new collaborative AgentTeam with a shared board and mailbox."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_name": {
                    "type": "string",
                    "description": "Name of the team to create",
                },
                "lead_agent_id": {
                    "type": "string",
                    "description": "Lead agent ID (default: lead)",
                },
            },
            "required": ["team_name"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        team_name = str(arguments.get("team_name", "")).strip()
        lead_id = str(arguments.get("lead_agent_id", "lead")).strip()
        if not team_name:
            return ToolResult(content="team_name is required", is_error=True)

        team = self.mgr.create_team(team_name=team_name, lead_agent_id=lead_id)
        return ToolResult(
            content=f"Team '{team.name}' created with lead '{team.lead_agent_id}'."
        )


class TeamDeleteTool(BaseTool):
    def __init__(self, mgr: TeamManager) -> None:
        self.mgr = mgr

    @property
    def name(self) -> str:
        return "TeamDelete"

    @property
    def description(self) -> str:
        return "Delete an AgentTeam workspace and clean all resources."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_name": {
                    "type": "string",
                    "description": "Name of the team to delete",
                }
            },
            "required": ["team_name"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        team_name = str(arguments.get("team_name", "")).strip()
        if not team_name:
            return ToolResult(content="team_name is required", is_error=True)

        ok = self.mgr.delete_team(team_name)
        if ok:
            return ToolResult(content=f"Team '{team_name}' deleted successfully.")
        return ToolResult(content=f"Team '{team_name}' not found.", is_error=True)


class TaskCreateTool(BaseTool):
    def __init__(self, mgr: TeamManager) -> None:
        self.mgr = mgr

    @property
    def name(self) -> str:
        return "TaskCreate"

    @property
    def description(self) -> str:
        return "Create a collaborative task on the team board."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "description": "Team name"},
                "subject": {"type": "string", "description": "Task title"},
                "description": {
                    "type": "string",
                    "description": "Detailed task instructions",
                },
                "blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs that must be completed before this task can start",
                },
            },
            "required": ["team_name", "subject"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        team_name = str(arguments.get("team_name", "")).strip()
        subject = str(arguments.get("subject", "")).strip()
        description = str(arguments.get("description", "")).strip()
        blocked_by = arguments.get("blocked_by", [])

        if not team_name or not subject:
            return ToolResult(
                content="team_name and subject are required", is_error=True
            )

        board = self.mgr.get_board(team_name)
        task = board.create_task(
            subject=subject,
            description=description,
            blocked_by=blocked_by if isinstance(blocked_by, list) else [],
        )
        return ToolResult(content=f"Task '{task.id}' ({task.subject}) created.")


class TaskGetTool(BaseTool):
    def __init__(self, mgr: TeamManager) -> None:
        self.mgr = mgr

    @property
    def name(self) -> str:
        return "TaskGet"

    @property
    def description(self) -> str:
        return "Retrieve task details from the team board."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "description": "Team name"},
                "task_id": {"type": "string", "description": "Task ID"},
            },
            "required": ["team_name", "task_id"],
        }

    @property
    def is_read_only(self) -> bool:
        return True

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        team_name = str(arguments.get("team_name", "")).strip()
        task_id = str(arguments.get("task_id", "")).strip()

        board = self.mgr.get_board(team_name)
        task = board.get_task(task_id)
        if not task:
            return ToolResult(content=f"Task '{task_id}' not found.", is_error=True)

        locked = board.is_task_locked(task_id)
        info = f"ID: {task.id}\nSubject: {task.subject}\nStatus: {task.status}\nOwner: {task.owner}\nLocked: {locked}\nBlocked By: {task.blocked_by}\nDescription: {task.description}"
        return ToolResult(content=info)


class TaskListTool(BaseTool):
    def __init__(self, mgr: TeamManager) -> None:
        self.mgr = mgr

    @property
    def name(self) -> str:
        return "TaskList"

    @property
    def description(self) -> str:
        return (
            "List tasks on the team board with status and dependency lock information."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "description": "Team name"},
                "status": {"type": "string", "description": "Filter by status"},
                "owner": {"type": "string", "description": "Filter by owner"},
            },
            "required": ["team_name"],
        }

    @property
    def is_read_only(self) -> bool:
        return True

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        team_name = str(arguments.get("team_name", "")).strip()
        status = arguments.get("status")
        owner = arguments.get("owner")

        board = self.mgr.get_board(team_name)
        tasks = board.list_tasks(status=status, owner=owner)

        lines = [f"=== Team Tasks for '{team_name}' ==="]
        for t in tasks:
            locked = " [LOCKED]" if board.is_task_locked(t.id) else ""
            lines.append(
                f"- [{t.status}]{locked} {t.id}: {t.subject} (Owner: {t.owner or 'Unassigned'})"
            )
        return ToolResult(content="\n".join(lines))


class TaskUpdateTool(BaseTool):
    def __init__(self, mgr: TeamManager) -> None:
        self.mgr = mgr

    @property
    def name(self) -> str:
        return "TaskUpdate"

    @property
    def description(self) -> str:
        return "Update task status, assign an owner, or add dependencies."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "description": "Team name"},
                "task_id": {"type": "string", "description": "Task ID"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed"],
                    "description": "New status",
                },
                "owner": {"type": "string", "description": "Assignee teammate name"},
                "add_blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional dependencies",
                },
            },
            "required": ["team_name", "task_id"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        team_name = str(arguments.get("team_name", "")).strip()
        task_id = str(arguments.get("task_id", "")).strip()
        status = arguments.get("status")
        owner = arguments.get("owner")
        add_blocked_by = arguments.get("add_blocked_by")

        board = self.mgr.get_board(team_name)
        try:
            task = board.update_task(
                task_id=task_id,
                status=status,
                owner=owner,
                add_blocked_by=add_blocked_by,
            )
            return ToolResult(
                content=f"Task '{task.id}' updated (Status: {task.status}, Owner: {task.owner})."
            )
        except Exception as e:
            return ToolResult(content=str(e), is_error=True)


class SendMessageTool(BaseTool):
    def __init__(self, mgr: TeamManager) -> None:
        self.mgr = mgr

    @property
    def name(self) -> str:
        return "SendMessage"

    @property
    def description(self) -> str:
        return "Send a message with a concise summary to a teammate or broadcast (*)."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "description": "Team name"},
                "to": {
                    "type": "string",
                    "description": "Target agent ID or '*' for broadcast",
                },
                "summary": {
                    "type": "string",
                    "description": "Short summary (5-10 words)",
                },
                "message": {"type": "string", "description": "Full message content"},
                "from_agent": {
                    "type": "string",
                    "description": "Sender agent ID (default: lead)",
                },
            },
            "required": ["team_name", "to", "summary", "message"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        team_name = str(arguments.get("team_name", "")).strip()
        to = str(arguments.get("to", "")).strip()
        summary = str(arguments.get("summary", "")).strip()
        message = str(arguments.get("message", "")).strip()
        sender = str(arguments.get("from_agent", "lead")).strip()

        if not team_name or not to or not summary or not message:
            return ToolResult(
                content="team_name, to, summary, and message are all required",
                is_error=True,
            )

        ok = self.mgr.send_message(
            team_name=team_name,
            sender_id=sender,
            to=to,
            summary=summary,
            message=message,
        )
        if ok:
            return ToolResult(content=f"Message sent to '{to}'.")
        return ToolResult(
            content=f"Failed to deliver message to '{to}'.", is_error=True
        )
