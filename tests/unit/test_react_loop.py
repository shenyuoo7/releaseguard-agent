import asyncio
from pathlib import Path
import pytest

from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.events import (
    StreamEnd,
    TextDelta,
    ThinkingDelta,
    ToolCallComplete,
)
from releaseguard_agent.llm.fake_stream_client import FakeStreamClient
from releaseguard_agent.runtime.events import (
    AgentErrorEvent,
    AgentLoopCompleteEvent,
    AgentToolResultEvent,
    AgentToolUseEvent,
    AgentTurnCompleteEvent,
)
from releaseguard_agent.runtime.react_engine import ReactAgentEngine
from releaseguard_agent.tools import ToolContext, build_default_tool_registry


@pytest.mark.anyio
async def test_react_loop_multi_turn_success(tmp_path: Path) -> None:
    # Setup sample file
    (tmp_path / "hello.py").write_text("print('hello world')", encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path)
    registry = build_default_tool_registry()

    # Turn 1: Call read_file
    turn1_events = [
        ThinkingDelta(thinking="I should read hello.py"),
        ToolCallComplete(
            tool_id="call_1",
            tool_name="read_file",
            arguments={"path": "hello.py"},
        ),
        StreamEnd(),
    ]

    # Turn 2: Terminal answer
    turn2_events = [
        TextDelta(text="The file prints hello world."),
        StreamEnd(),
    ]

    client = FakeStreamClient(turns=[turn1_events, turn2_events])
    engine = ReactAgentEngine(client=client, registry=registry, max_turns=5)

    conv = ConversationManager()
    conv.add_user_message("What does hello.py do?")

    events = []
    async for ev in engine.run(conv, context=ctx):
        events.append(ev)

    # Assertions
    tool_uses = [e for e in events if isinstance(e, AgentToolUseEvent)]
    assert len(tool_uses) == 1
    assert tool_uses[0].tool_name == "read_file"

    tool_results = [e for e in events if isinstance(e, AgentToolResultEvent)]
    assert len(tool_results) == 1
    assert "print('hello world')" in tool_results[0].result.content

    turns = [e for e in events if isinstance(e, AgentTurnCompleteEvent)]
    assert len(turns) == 2
    assert turns[0].turn == 1
    assert turns[1].turn == 2

    loop_completes = [e for e in events if isinstance(e, AgentLoopCompleteEvent)]
    assert len(loop_completes) == 1
    assert loop_completes[0].total_turns == 2
    assert loop_completes[0].final_content == "The file prints hello world."

    # Check conversation history
    messages = conv.get_messages()
    assert len(messages) == 4
    assert messages[0].role == "user"  # Initial query
    assert messages[1].role == "assistant"  # Turn 1 tool call
    assert messages[2].role == "user"  # Tool result
    assert messages[3].role == "assistant"  # Final response


@pytest.mark.anyio
async def test_react_loop_max_turns_aborts(tmp_path: Path) -> None:
    registry = build_default_tool_registry()
    ctx = ToolContext(cwd=tmp_path)

    # Endless tool caller
    infinite_tool_events = [
        ToolCallComplete(
            tool_id="call_loop",
            tool_name="glob",
            arguments={"pattern": "*.py"},
        ),
        StreamEnd(),
    ]

    # Set up client to repeat tool calls
    client = FakeStreamClient(events=infinite_tool_events)
    engine = ReactAgentEngine(client=client, registry=registry, max_turns=2)

    conv = ConversationManager()
    conv.add_user_message("Loop forever")

    events = [ev async for ev in engine.run(conv, context=ctx)]

    errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    assert len(errors) == 1
    assert "Maximum iteration limit (2 turns) reached" in errors[0].error


@pytest.mark.anyio
async def test_react_loop_cancel_token(tmp_path: Path) -> None:
    registry = build_default_tool_registry()
    ctx = ToolContext(cwd=tmp_path)

    client = FakeStreamClient(text_chunks=["Answer"])
    engine = ReactAgentEngine(client=client, registry=registry)

    cancel_token = asyncio.Event()
    cancel_token.set()  # Cancelled before start

    conv = ConversationManager()
    conv.add_user_message("Stop me")

    events = [
        ev async for ev in engine.run(conv, cancel_token=cancel_token, context=ctx)
    ]
    errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    assert len(errors) == 1
    assert "cancelled by user" in errors[0].error


@pytest.mark.anyio
async def test_react_loop_unknown_tool_circuit_breaker(tmp_path: Path) -> None:
    registry = build_default_tool_registry()
    ctx = ToolContext(cwd=tmp_path)

    unknown_events = [
        ToolCallComplete(
            tool_id="u1",
            tool_name="non_existent_tool_xyz",
            arguments={},
        ),
        StreamEnd(),
    ]

    client = FakeStreamClient(events=unknown_events)
    engine = ReactAgentEngine(client=client, registry=registry, max_turns=10)

    conv = ConversationManager()
    conv.add_user_message("Call broken tool")

    events = [ev async for ev in engine.run(conv, context=ctx)]

    errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    assert len(errors) == 1
    assert (
        "Circuit breaker triggered: 3 consecutive unknown tool calls" in errors[0].error
    )


@pytest.mark.anyio
async def test_react_loop_plan_mode(tmp_path: Path) -> None:
    registry = build_default_tool_registry()
    ctx = ToolContext(cwd=tmp_path)

    terminal_events = [TextDelta(text="Plan proposed."), StreamEnd()]
    client = FakeStreamClient(events=terminal_events)
    engine = ReactAgentEngine(client=client, registry=registry)

    conv = ConversationManager()
    conv.add_user_message("Design plan")

    events = [ev async for ev in engine.run(conv, plan_mode=True, context=ctx)]
    assert any(isinstance(e, AgentLoopCompleteEvent) for e in events)

    # Verify that plan mode prompt was injected and write tools were hidden
    last_call = client.calls[0]
    assert "# Plan Mode Active" in last_call["system"]
    tool_names = [t["name"] for t in last_call["tools"]]
    assert "read_file" in tool_names
    assert "glob" in tool_names
    assert "grep" in tool_names
    assert "write_file" not in tool_names
    assert "edit_file" not in tool_names
    assert "bash" not in tool_names
