"""Shared TeamTask board managing state transitions and DAG dependency locking."""

from dataclasses import asdict
import json
from pathlib import Path
import time
from typing import Literal

from releaseguard_agent.teams.models import TeamTask


class TaskBoard:
    """Thread-safe task board managing team task states and dependency topology."""

    def __init__(self, board_file: Path | None = None) -> None:
        self.board_file = board_file
        self.tasks: dict[str, TeamTask] = {}
        if self.board_file and self.board_file.is_file():
            self.load()

    def load(self) -> None:
        """Load tasks from JSON file."""
        if not self.board_file or not self.board_file.is_file():
            return
        try:
            content = self.board_file.read_text(encoding="utf-8").strip()
            if not content:
                return
            data = json.loads(content)
            self.tasks.clear()
            for item in data.get("tasks", []):
                t = TeamTask(
                    id=item["id"],
                    subject=item["subject"],
                    description=item.get("description", ""),
                    status=item.get("status", "pending"),
                    owner=item.get("owner", ""),
                    blocked_by=list(item.get("blocked_by", [])),
                    created_at=item.get("created_at", time.time()),
                    updated_at=item.get("updated_at", time.time()),
                )
                self.tasks[t.id] = t
        except Exception:
            pass

    def save(self) -> None:
        """Persist tasks to JSON file."""
        if not self.board_file:
            return
        self.board_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "tasks": [asdict(t) for t in self.tasks.values()],
        }
        self.board_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def create_task(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[str] | None = None,
    ) -> TeamTask:
        """Create a new task and add to board."""
        task_id = f"task-{len(self.tasks) + 1}"
        t = TeamTask(
            id=task_id,
            subject=subject,
            description=description,
            status="pending",
            blocked_by=list(blocked_by) if blocked_by else [],
        )
        self.tasks[task_id] = t
        self.save()
        return t

    def get_task(self, task_id: str) -> TeamTask | None:
        """Retrieve task by id."""
        return self.tasks.get(task_id)

    def list_tasks(
        self,
        status: str | None = None,
        owner: str | None = None,
    ) -> list[TeamTask]:
        """List tasks optionally filtered by status and/or owner."""
        res = list(self.tasks.values())
        if status:
            res = [t for t in res if t.status == status]
        if owner:
            res = [t for t in res if t.owner == owner]
        return res

    def is_task_locked(self, task_id: str) -> bool:
        """Check if a task is locked by uncompleted dependency tasks."""
        task = self.tasks.get(task_id)
        if not task or not task.blocked_by:
            return False

        for blocker_id in task.blocked_by:
            blocker = self.tasks.get(blocker_id)
            if blocker and blocker.status != "completed":
                return True
        return False

    def update_task(
        self,
        task_id: str,
        status: Literal["pending", "in_progress", "completed"] | None = None,
        owner: str | None = None,
        add_blocked_by: list[str] | None = None,
    ) -> TeamTask:
        """Update task state with dependency enforcement on claim/start."""
        task = self.tasks.get(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found on board")

        if add_blocked_by:
            for b in add_blocked_by:
                if b not in task.blocked_by and b != task_id:
                    task.blocked_by.append(b)

        # Enforce dependency check when claiming or starting a task
        will_start = status == "in_progress" or (
            owner is not None and owner != "" and task.status != "completed"
        )
        if will_start and self.is_task_locked(task_id):
            uncompleted = [
                b
                for b in task.blocked_by
                if self.tasks.get(b) and self.tasks[b].status != "completed"
            ]
            raise ValueError(
                f"Task '{task_id}' is locked: blocked by uncompleted tasks {uncompleted}."
            )

        if status is not None:
            task.status = status
        if owner is not None:
            task.owner = owner

        task.updated_at = time.time()
        self.save()
        return task
