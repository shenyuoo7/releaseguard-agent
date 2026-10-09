"""Unit tests for shared TaskBoard DAG dependencies, Coordinator tool filtering, and TeamManager."""

from pathlib import Path
from typing import Any
import pytest

from releaseguard_agent.teams.board import TaskBoard
from releaseguard_agent.teams.coordinator import filter_tools_for_coordinator
from releaseguard_agent.teams.manager import TeamManager
from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult
from releaseguard_agent.tools.registry import ToolRegistry


class DummyTool(BaseTool):
    def __init__(self, name: str, read_only: bool = True) -> None:
        self._name = name
        self._ro = read_only

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object"}

    @property
    def is_read_only(self) -> bool:
        return self._ro

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        return ToolResult(content="ok")


def test_task_board_crud_and_persistence(tmp_path: Path) -> None:
    board_file = tmp_path / "board.json"
    board = TaskBoard(board_file=board_file)

    t1 = board.create_task("T1: Explore repo", "Find issues")
    board.create_task("T2: Run pytest", "Verify fix")
    assert len(board.list_tasks()) == 2

    # Reload from file
    board2 = TaskBoard(board_file=board_file)
    assert len(board2.list_tasks()) == 2
    assert board2.get_task(t1.id) is not None


def test_dag_dependency_locking_and_unlocking(tmp_path: Path) -> None:
    board = TaskBoard(board_file=tmp_path / "board.json")

    t1 = board.create_task("T1: Fix core bug")
    t2 = board.create_task("T2: Verify fix", blocked_by=[t1.id])

    # t2 is initially locked because t1 is pending
    assert board.is_task_locked(t2.id) is True

    # Claiming or starting t2 must raise ValueError
    with pytest.raises(ValueError) as exc_info:
        board.update_task(t2.id, status="in_progress")
    assert "is locked" in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info:
        board.update_task(t2.id, owner="alice")
    assert "is locked" in str(exc_info.value)

    # Complete t1 -> t2 must unlock
    board.update_task(t1.id, status="completed")
    assert board.is_task_locked(t2.id) is False

    # Now t2 can be assigned and moved to in_progress
    updated = board.update_task(t2.id, status="in_progress", owner="alice")
    assert updated.status == "in_progress"
    assert updated.owner == "alice"


def test_coordinator_mode_tool_filtering() -> None:
    registry = ToolRegistry()
    registry.register(DummyTool("Agent"))
    registry.register(DummyTool("SendMessage"))
    registry.register(DummyTool("TaskCreate"))
    registry.register(DummyTool("ReadFile"))
    registry.register(DummyTool("Bash"))
    registry.register(DummyTool("WriteFile", read_only=False))
    registry.register(DummyTool("EditFile", read_only=False))

    coordinator_registry = filter_tools_for_coordinator(registry)
    names = {t.name for t in coordinator_registry.list_tools()}

    # Coordinator Lead retains coordination and read/bash tools
    assert "Agent" in names
    assert "SendMessage" in names
    assert "TaskCreate" in names
    assert "ReadFile" in names
    assert "Bash" in names

    # Coordinator Lead is strictly stripped of file mutation tools
    assert "WriteFile" not in names
    assert "EditFile" not in names


def test_team_manager_lifecycle(tmp_path: Path) -> None:
    mgr = TeamManager(base_dir=tmp_path / "teams")
    team = mgr.create_team("release-alpha", lead_agent_id="coordinator-1")

    assert team.name == "release-alpha"
    assert team.lead_agent_id == "coordinator-1"

    # Send and read message
    ok = mgr.send_message(
        team_name="release-alpha",
        sender_id="coordinator-1",
        to="worker-1",
        summary="task assigned",
        message="Please check issue #42",
    )
    assert ok is True

    messages = mgr.read_messages("release-alpha", "worker-1")
    assert len(messages) == 1
    assert messages[0]["summary"] == "task assigned"

    # Delete team
    assert mgr.delete_team("release-alpha") is True
    assert mgr.get_team("release-alpha") is None
