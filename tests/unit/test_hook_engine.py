"""Unit tests for HookEngine, lifecycle actions, YAML merging, and ReAct loop interception."""

import asyncio
from pathlib import Path
from typing import Any
import pytest

from releaseguard_agent.hooks.engine import HookEngine
from releaseguard_agent.hooks.models import (
    HookAction,
    HookContext,
    HookDef,
    ToolRejectedError,
)
from releaseguard_agent.llm.fake_stream_client import FakeStreamClient
from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.events import StreamEnd, TextDelta, ToolCallComplete
from releaseguard_agent.runtime.events import (
    AgentLoopCompleteEvent,
    AgentToolResultEvent,
)
from releaseguard_agent.runtime.react_engine import ReactAgentEngine
from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult
from releaseguard_agent.tools.registry import ToolRegistry


def test_hook_context_variable_expansion() -> None:
    ctx = HookContext(
        event_name="post_tool_use",
        workspace_root="/app/repo",
        tool_name="WriteFile",
        tool_args={"path": "src/app.py", "encoding": "utf-8"},
        file_path="src/app.py",
        message="file written",
        error="",
    )
    res = ctx.expand(
        "ruff format $FILE_PATH on $EVENT using $TOOL_NAME ($TOOL_ARGS.encoding)"
    )
    assert res == "ruff format src/app.py on post_tool_use using WriteFile (utf-8)"


def test_hook_yaml_loading_and_append_merging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Setup mock user home config
    fake_home = tmp_path / "home"
    fake_home_rg = fake_home / ".releaseguard"
    fake_home_rg.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    user_yaml = fake_home_rg / "hooks.yaml"
    user_yaml.write_text(
        """
hooks:
  - id: user-notify
    event: session_start
    action:
      type: prompt
      message: "Welcome from user hook"
""",
        encoding="utf-8",
    )

    # 2. Setup mock project config
    proj_root = tmp_path / "project"
    proj_rg = proj_root / ".releaseguard"
    proj_rg.mkdir(parents=True)

    proj_yaml = proj_rg / "hooks.yaml"
    proj_yaml.write_text(
        """
hooks:
  - id: proj-block
    event: pre_tool_use
    condition: 'args.path ~= "vendor/*"'
    reject: true
    reason: "vendor files are protected"
""",
        encoding="utf-8",
    )

    engine = HookEngine(workspace_root=proj_root)
    assert len(engine.hooks) == 2
    assert engine.hooks[0].id == "user-notify"
    assert engine.hooks[1].id == "proj-block"
    assert engine.hooks[1].reject is True


@pytest.mark.anyio
async def test_pre_tool_synchronous_interception() -> None:
    engine = HookEngine(
        hooks=[
            HookDef(
                id="block-vendor",
                event="pre_tool_use",
                condition_str='args.path ~= "vendor/*"',
                reject=True,
                reason="Protected: $FILE_PATH cannot be modified",
            )
        ]
    )

    # Allowed path
    allowed_ctx = HookContext(
        event_name="pre_tool_use",
        tool_name="WriteFile",
        tool_args={"path": "src/main.py"},
    )
    # Should not raise
    await engine.run_pre_tool_hooks(allowed_ctx)

    # Blocked path
    blocked_ctx = HookContext(
        event_name="pre_tool_use",
        tool_name="WriteFile",
        tool_args={"path": "vendor/dep.py"},
    )
    with pytest.raises(ToolRejectedError) as exc_info:
        await engine.run_pre_tool_hooks(blocked_ctx)
    assert "Protected: vendor/dep.py cannot be modified" in str(exc_info.value)


@pytest.mark.anyio
async def test_once_and_reset_session() -> None:
    class CountingHookEngine(HookEngine):
        pass

    engine = CountingHookEngine(
        hooks=[
            HookDef(
                id="init-once",
                event="turn_start",
                action=HookAction(action_type="prompt", message="Initialized"),
                once=True,
            )
        ]
    )

    ctx = HookContext(event_name="turn_start")
    out1 = await engine.emit("turn_start", ctx)
    assert len(out1) == 1
    assert "Initialized" in out1[0]

    # Second emit should be skipped because once=True
    out2 = await engine.emit("turn_start", ctx)
    assert len(out2) == 0

    # Reset session restores hook execution
    engine.reset_session()
    out3 = await engine.emit("turn_start", ctx)
    assert len(out3) == 1


@pytest.mark.anyio
async def test_error_isolation_does_not_crash_main_loop() -> None:
    engine = HookEngine(
        hooks=[
            HookDef(
                id="broken-command",
                event="post_tool_use",
                action=HookAction(
                    action_type="command",
                    command="non_existent_command_12345_xyz",
                    timeout=2.0,
                ),
            ),
            HookDef(
                id="broken-http",
                event="post_tool_use",
                action=HookAction(
                    action_type="http",
                    url="http://127.0.0.1:59999/unreachable",
                    timeout=0.5,
                ),
                async_exec=True,
            ),
        ]
    )

    ctx = HookContext(event_name="post_tool_use", tool_name="ReadFile")
    # Must execute cleanly without unhandled exceptions
    await engine.emit("post_tool_use", ctx)
    # Give async task a brief moment to run and catch error safely
    await asyncio.sleep(0.05)


class DummyWriteTool(BaseTool):
    @property
    def name(self) -> str:
        return "WriteFile"

    @property
    def description(self) -> str:
        return "Write file"

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        return ToolResult(content=f"Successfully wrote {arguments.get('path')}")


@pytest.mark.anyio
async def test_react_engine_pre_tool_hook_rejection_flow() -> None:
    # 1. Setup registry with dummy tool
    registry = ToolRegistry()
    registry.register(DummyWriteTool())

    # 2. Setup hook engine with rejection hook
    hook_engine = HookEngine(
        hooks=[
            HookDef(
                id="guard-vendor",
                event="pre_tool_use",
                condition_str='args.path ~= "vendor/*"',
                reject=True,
                reason="Security policy: vendor/ is protected",
            )
        ]
    )

    # 3. Setup fake LLM client:
    # Turn 1: model attempts to write to vendor/lib.py
    # Turn 2: model sees error and provides final answer
    stream1: list[Any] = [
        ToolCallComplete(
            tool_id="call_1",
            tool_name="WriteFile",
            arguments={"path": "vendor/lib.py", "content": "bad code"},
        ),
        StreamEnd(),
    ]
    stream2: list[Any] = [
        TextDelta(text="Understood. The vendor directory is protected, so I stopped."),
        StreamEnd(),
    ]
    client = FakeStreamClient(turns=[stream1, stream2])

    react_engine = ReactAgentEngine(
        client=client,
        registry=registry,
        max_turns=5,
        hook_engine=hook_engine,
    )

    conv = ConversationManager()
    conv.add_user_message("Please update vendor/lib.py")

    events = []
    async for ev in react_engine.run(conv):
        events.append(ev)

    # Verify tool execution was rejected and converted into ToolResult(is_error=True)
    tool_results = [e for e in events if isinstance(e, AgentToolResultEvent)]
    assert len(tool_results) == 1
    assert tool_results[0].result.is_error is True
    assert "Security policy: vendor/ is protected" in tool_results[0].result.content

    # Verify loop successfully completed after model handled error
    loop_complete = [e for e in events if isinstance(e, AgentLoopCompleteEvent)]
    assert len(loop_complete) == 1
    assert "vendor directory is protected" in loop_complete[0].final_content
