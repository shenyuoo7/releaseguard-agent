from dataclasses import FrozenInstanceError
import pytest

from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.events import (
    StreamEnd,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallDelta,
    ToolCallComplete,
    ToolCallStart,
)
from releaseguard_agent.llm.messages import (
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def test_stream_events_immutability() -> None:
    text_delta = TextDelta(text="hello")
    with pytest.raises(FrozenInstanceError):
        # pyright: ignore
        text_delta.text = "world"  # type: ignore[misc]

    think_delta = ThinkingDelta(thinking="reasoning...")
    with pytest.raises(FrozenInstanceError):
        think_delta.thinking = "other"  # type: ignore[misc]

    tool_start = ToolCallStart(tool_id="call_1", tool_name="grep")
    with pytest.raises(FrozenInstanceError):
        tool_start.tool_name = "find"  # type: ignore[misc]

    tool_delta = ToolCallDelta(tool_id="call_1", arguments_delta='{"pattern"')
    assert tool_delta.arguments_delta == '{"pattern"'

    tool_complete = ToolCallComplete(
        tool_id="call_1",
        tool_name="grep",
        arguments={"pattern": "TODO"},
    )
    assert tool_complete.arguments["pattern"] == "TODO"

    think_complete = ThinkingComplete()
    assert isinstance(think_complete, ThinkingComplete)

    end_event = StreamEnd(usage={"input_tokens": 10, "output_tokens": 20})
    assert end_event.usage == {"input_tokens": 10, "output_tokens": 20}


def test_messages_structure_and_roundtrip() -> None:
    thinking = ThinkingBlock(thinking="Let me review the rule", signature="sig_abc")
    tool_use = ToolUseBlock(
        tool_use_id="tu_123",
        tool_name="file_read",
        arguments={"path": "main.py"},
    )
    tool_result = ToolResultBlock(
        tool_use_id="tu_123",
        content="print('hello')",
        is_error=False,
    )

    msg = Message(
        role="assistant",
        content="Here is the file content.",
        thinking_blocks=[thinking],
        tool_uses=[tool_use],
        tool_results=[tool_result],
    )

    payload = msg.to_dict()
    assert payload["role"] == "assistant"
    assert payload["content"] == "Here is the file content."
    assert payload["thinking_blocks"][0]["thinking"] == "Let me review the rule"
    assert payload["tool_uses"][0]["tool_name"] == "file_read"
    assert payload["tool_results"][0]["content"] == "print('hello')"

    reconstructed = Message.from_dict(payload)
    assert reconstructed.role == msg.role
    assert reconstructed.content == msg.content
    assert len(reconstructed.thinking_blocks) == 1
    assert reconstructed.thinking_blocks[0].signature == "sig_abc"
    assert reconstructed.tool_uses[0].arguments == {"path": "main.py"}
    assert not reconstructed.tool_results[0].is_error

    cloned = msg.clone()
    assert cloned.role == msg.role
    cloned.content = "Changed"
    assert msg.content == "Here is the file content."


def test_conversation_manager() -> None:
    conv = ConversationManager()
    assert len(conv.get_messages()) == 0
    assert conv.estimate_tokens() == 0

    conv.add_user_message("What is ReleaseGuard?")
    assert len(conv.get_messages()) == 1
    assert conv.get_messages()[0].role == "user"
    assert conv.get_messages()[0].content == "What is ReleaseGuard?"

    conv.add_assistant_message(
        text="ReleaseGuard is a release review agent.",
        thinking=[ThinkingBlock(thinking="Analyze query")],
    )
    assert len(conv.get_messages()) == 2
    assert conv.get_messages()[1].role == "assistant"
    assert conv.get_messages()[1].thinking_blocks[0].thinking == "Analyze query"

    assert conv.estimate_tokens() > 0

    clone = conv.clone()
    assert len(clone.get_messages()) == 2
    conv.clear()
    assert len(conv.get_messages()) == 0
    assert len(clone.get_messages()) == 2
