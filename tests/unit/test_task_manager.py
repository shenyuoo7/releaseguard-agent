"""Unit tests for TaskManager background lifecycle and AgentTool delegation."""

import asyncio
from typing import Any
import pytest

from releaseguard_agent.llm.events import StreamEnd, TextDelta, ToolCallComplete
from releaseguard_agent.llm.fake_stream_client import FakeStreamClient
from releaseguard_agent.subagent.task_manager import TaskManager
from releaseguard_agent.subagent.tool import AgentTool
from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult
from releaseguard_agent.tools.registry import ToolRegistry


class SimpleReadTool(BaseTool):
    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read file tool"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    @property
    def is_read_only(self) -> bool:
        return True

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        return ToolResult(content=f"Contents of {arguments.get('path', 'unknown')}")


@pytest.mark.anyio
async def test_task_manager_launch_and_drain_notifications() -> None:
    tm = TaskManager()

    async def _sample_work() -> str:
        await asyncio.sleep(0.01)
        return "Analysis completed: 0 vulnerabilities found."

    task = tm.launch("task-101", "Security Audit", _sample_work())
    assert task.status == "running"

    # Wait for background task to complete
    await asyncio.sleep(0.05)
    assert task.status == "completed"
    assert task.result == "Analysis completed: 0 vulnerabilities found."

    notifications = tm.get_pending_notifications()
    assert len(notifications) == 1
    assert "<task-id>task-101</task-id>" in notifications[0]
    assert "<status>completed</status>" in notifications[0]
    assert "0 vulnerabilities found" in notifications[0]

    # Queue should now be empty
    assert len(tm.get_pending_notifications()) == 0


@pytest.mark.anyio
async def test_task_manager_failure_isolation() -> None:
    tm = TaskManager()

    async def _failing_work() -> str:
        await asyncio.sleep(0.01)
        raise RuntimeError("Network timeout contacting remote registry")

    task = tm.launch("task-err", "Broken Task", _failing_work())
    await asyncio.sleep(0.05)

    assert task.status == "failed"
    assert "Network timeout contacting remote registry" in task.error

    notifications = tm.get_pending_notifications()
    assert len(notifications) == 1
    assert "<status>failed</status>" in notifications[0]
    assert "Network timeout" in notifications[0]


@pytest.mark.anyio
async def test_task_manager_adopt_running() -> None:
    tm = TaskManager()

    async def _long_running() -> str:
        await asyncio.sleep(0.02)
        return "Adopted work finished"

    running_task = asyncio.create_task(_long_running())
    adopted = tm.adopt_running("task-adopted", "Heavy Task", running_task)
    assert adopted.status == "running"

    await asyncio.sleep(0.06)
    assert adopted.status == "completed"
    assert adopted.result == "Adopted work finished"

    notifs = tm.get_pending_notifications()
    assert len(notifs) == 1
    assert "<task-id>task-adopted</task-id>" in notifs[0]


@pytest.mark.anyio
async def test_agent_tool_delegates_to_explore_expert() -> None:
    parent_registry = ToolRegistry()
    parent_registry.register(SimpleReadTool())

    # Turn 1: Model calls read_file
    turn1: list[Any] = [
        ToolCallComplete(
            tool_id="c1",
            tool_name="read_file",
            arguments={"path": "src/core.py"},
        ),
        StreamEnd(),
    ]
    # Turn 2: Model finishes answer
    turn2: list[Any] = [
        TextDelta(text="Exploration complete: found core logic in src/core.py."),
        StreamEnd(),
    ]
    client = FakeStreamClient(turns=[turn1, turn2])

    agent_tool = AgentTool(
        llm_client=client,
        parent_registry=parent_registry,
    )
    parent_registry.register(agent_tool)

    res = await agent_tool.execute(
        arguments={
            "prompt": "Inspect src/core.py and report back",
            "description": "Codebase exploration",
            "subagent_type": "Explore",
        }
    )

    assert res.is_error is False
    assert "Exploration complete" in res.content
    assert "src/core.py" in res.content


@pytest.mark.anyio
async def test_agent_tool_fork_mode_runs_in_background() -> None:
    parent_registry = ToolRegistry()
    parent_registry.register(SimpleReadTool())

    turn1: list[Any] = [
        TextDelta(text="Background worker completed task smoothly."),
        StreamEnd(),
    ]
    client = FakeStreamClient(turns=[turn1])
    tm = TaskManager()

    agent_tool = AgentTool(
        llm_client=client,
        parent_registry=parent_registry,
        task_manager=tm,
    )

    # Calling without subagent_type triggers Fork mode (forced background)
    res = await agent_tool.execute(
        arguments={
            "prompt": "Run deep static analysis",
            "description": "Forked background worker",
        }
    )

    assert res.is_error is False
    assert "已成功启动" in res.content
    assert res.metadata.get("async") is True

    # Wait for background task to complete
    await asyncio.sleep(0.06)
    notifs = tm.get_pending_notifications()
    assert len(notifs) == 1
    assert "Background worker completed task smoothly" in notifs[0]
