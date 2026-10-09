from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
import pytest

from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.events import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCallComplete,
)
from releaseguard_agent.llm.fake_stream_client import FakeStreamClient
from releaseguard_agent.llm.messages import Message
from releaseguard_agent.runtime.context.auto_compact import (
    COMPACT_SYSTEM_PROMPT,
    compute_compact_threshold,
    extract_structured_summary,
    perform_auto_compact,
)
from releaseguard_agent.runtime.context.restoration import (
    BOUNDARY_MESSAGE,
    restore_compacted_conversation,
)
from releaseguard_agent.runtime.context.token_counter import (
    count_message_tokens,
    count_text_tokens,
    estimate_context_tokens,
)
from releaseguard_agent.runtime.events import AgentErrorEvent
from releaseguard_agent.runtime.react_engine import ReactAgentEngine
from releaseguard_agent.tools import ToolContext, build_default_tool_registry


def test_token_counter_calculations() -> None:
    """T1: Verify tiktoken counting on text and message structures."""
    assert count_text_tokens("") == 0
    assert count_text_tokens("hello world") > 0
    assert count_text_tokens("这是一段中文测试") > 0

    msg = Message(role="user", content="hello world")
    assert count_message_tokens(msg) > count_text_tokens("hello world")

    total = estimate_context_tokens([msg], system_prompt="system instructions")
    assert total > count_message_tokens(msg)


def test_compact_threshold_formula() -> None:
    """T3 & AC3: Verify threshold calculation with safety margin."""
    # 200,000 - 20,000 - 13,000 = 167,000
    th = compute_compact_threshold(
        window_tokens=200_000, reserve_tokens=20_000, safety_margin=13_000
    )
    assert th == 167_000


def test_extract_structured_summary_two_phase() -> None:
    """T3 & AC3: Draft <analysis> is discarded; only <summary> block is extracted."""
    mock_llm_output = """
<analysis>
This is the internal draft reasoning exploring what happened during turns 1-10.
The user asked to fix tests, we read main.py, encountered NameError, and fixed it.
</analysis>

<summary>
(1) 主要请求与用户意图: 修复单元测试
(2) 关键技术概念: Python ast, pytest
(3) 涉及文件: src/main.py
(4) 错误与修复: NameError: foo is not defined
(5) 解决过程: 引入了缺失的导入语句
(6) 用户原始消息: "请帮我修复测试"
(7) 待办事项: 无
(8) 当前工作: 刚刚完成修复
(9) 下一步: 运行回归测试
</summary>
"""
    summary = extract_structured_summary(mock_llm_output)
    assert "<analysis>" not in summary
    assert "internal draft reasoning" not in summary
    assert "(1) 主要请求与用户意图: 修复单元测试" in summary
    assert "(9) 下一步: 运行回归测试" in summary


def test_restore_compacted_conversation(tmp_path: Path) -> None:
    """T4 & AC4: Reconstructs summary, boundary, and restores key accessed files."""
    test_file = tmp_path / "app.py"
    test_file.write_text("print('core app code')", encoding="utf-8")

    summary_text = "Summary of past turns"
    recent = [
        Message(role="user", content="latest query"),
        Message(role="assistant", content="latest answer"),
    ]

    restored = restore_compacted_conversation(
        summary_text=summary_text,
        recent_messages=recent,
        accessed_file_paths=[test_file],
        max_files=5,
    )

    # 1. Summary message
    assert restored[0].role == "user"
    assert "Summary of past turns" in restored[0].content

    # 2. Boundary message
    assert restored[1].role == "user"
    assert BOUNDARY_MESSAGE in restored[1].content

    # 3. Restored file
    assert restored[2].role == "user"
    assert "print('core app code')" in restored[2].content

    # 4. Recent messages
    assert restored[3].content == "latest query"
    assert restored[4].content == "latest answer"


@pytest.mark.anyio
async def test_auto_compact_execution_success() -> None:
    """T3 & AC3: perform_auto_compact drives LLM stream and updates conversation in-place."""
    summary_stream: list[StreamEvent] = [
        TextDelta(
            text="<analysis>thinking</analysis>\n<summary>(1) Request: clean up\n(2) Details...</summary>"
        ),
        StreamEnd(),
    ]
    client = FakeStreamClient(events=summary_stream)

    conv = ConversationManager()
    # Add 8 turns of messages
    for i in range(8):
        conv.add_user_message(f"User message {i}")
        conv.add_assistant_message(f"Assistant message {i}")

    success = await perform_auto_compact(
        conversation=conv,
        client=client,
        keep_recent_messages=2,
    )
    assert success is True

    messages = conv.get_messages()
    # History was replaced with summary + boundary + 2 preserved recent messages
    assert len(messages) == 4
    assert "历史对话结构化摘要" in messages[0].content
    assert BOUNDARY_MESSAGE in messages[1].content
    assert messages[2].content == "User message 7"
    assert messages[3].content == "Assistant message 7"


@pytest.mark.anyio
async def test_compact_failure_circuit_breaker(tmp_path: Path) -> None:
    """AC5: Three consecutive compact failures trigger circuit breaker abort."""
    registry = build_default_tool_registry()
    ctx = ToolContext(cwd=tmp_path)
    (tmp_path / "dummy.txt").write_text("dummy test content", encoding="utf-8")

    class FailingCompactClient:
        def __init__(self) -> None:
            self.call_count = 0

        async def stream(
            self,
            conversation: ConversationManager,
            system: str = "",
            tools: list[dict[str, Any]] | None = None,
        ) -> AsyncIterator[StreamEvent]:
            self.call_count += 1
            if system == COMPACT_SYSTEM_PROMPT:
                # Compaction call -> fails because output is empty
                yield TextDelta(text="")
                yield StreamEnd()
            else:
                # Main agent call -> yields a valid tool call so the agent loop continues
                yield ToolCallComplete(
                    tool_id=f"call_{self.call_count}",
                    tool_name="read_file",
                    arguments={"path": "dummy.txt"},
                )
                yield StreamEnd()

        async def close(self) -> None:
            pass

    client = FailingCompactClient()

    # Set threshold very low (50 tokens) so auto-compact triggers on every turn
    engine = ReactAgentEngine(
        client=client,
        registry=registry,
        max_turns=10,
        window_tokens=33_050,  # threshold = 33050 - 33000 = 50 tokens
    )

    conv = ConversationManager()
    # Pre-populate with enough tokens to exceed the 1000 minimum threshold
    conv.add_user_message(
        "Large conversation message with repeated context tokens. " * 200
    )
    conv.add_assistant_message(
        "Assistant response with substantial tokens for testing. " * 200
    )

    events = [ev async for ev in engine.run(conv, context=ctx)]

    # Assert circuit breaker triggered after 3 failures
    errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    assert len(errors) == 1
    assert "3 consecutive Auto-Compact failures" in errors[0].error
