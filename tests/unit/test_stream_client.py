import httpx
import pytest

from releaseguard_agent.config.provider import ProviderConfig
from releaseguard_agent.llm.anthropic_stream_client import AnthropicStreamClient
from releaseguard_agent.llm.client import LLMStreamError
from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.events import (
    StreamEnd,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
)
from releaseguard_agent.llm.fake_stream_client import FakeStreamClient
from releaseguard_agent.llm.openai_stream_client import OpenAIStreamClient


@pytest.mark.anyio
async def test_fake_stream_client_text_and_thinking() -> None:
    client = FakeStreamClient(
        thinking_chunks=["Thinking step 1", " Thinking step 2"],
        text_chunks=["Hello, ", "world!"],
    )

    conv = ConversationManager()
    conv.add_user_message("Hi")

    events = []
    async for event in client.stream(conv, system="System prompt"):
        events.append(event)

    assert len(client.calls) == 1
    assert client.calls[0]["system"] == "System prompt"

    assert isinstance(events[0], ThinkingDelta)
    assert events[0].thinking == "Thinking step 1"
    assert isinstance(events[1], ThinkingDelta)
    assert events[1].thinking == " Thinking step 2"
    assert isinstance(events[2], ThinkingComplete)
    assert isinstance(events[3], TextDelta)
    assert events[3].text == "Hello, "
    assert isinstance(events[4], TextDelta)
    assert events[4].text == "world!"
    assert isinstance(events[5], StreamEnd)

    await client.close()
    assert client.closed is True


@pytest.mark.anyio
async def test_openai_stream_client_think_tags_separation() -> None:
    sse_body = (
        'data: {"choices":[{"delta":{"content":"<think>Plan quicksort</think>def sort(): pass"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=sse_body,
        )

    transport = httpx.MockTransport(mock_handler)
    http_client = httpx.AsyncClient(transport=transport)

    config = ProviderConfig(
        name="test-openai",
        type="openai",
        api_key="sk-test",
        model="gpt-4o",
    )
    client = OpenAIStreamClient(config=config, http_client=http_client)

    conv = ConversationManager()
    conv.add_user_message("Write sort")

    events = [ev async for ev in client.stream(conv)]

    # Verification: thinking is completely isolated from text
    thinking_texts = [e.thinking for e in events if isinstance(e, ThinkingDelta)]
    regular_texts = [e.text for e in events if isinstance(e, TextDelta)]

    assert thinking_texts == ["Plan quicksort"]
    assert regular_texts == ["def sort(): pass"]
    assert any(isinstance(e, ThinkingComplete) for e in events)
    assert any(isinstance(e, StreamEnd) for e in events)

    await client.close()
    await http_client.aclose()


@pytest.mark.anyio
async def test_openai_stream_client_reasoning_content() -> None:
    sse_body = (
        'data: {"choices":[{"delta":{"reasoning_content":"Step 1 reasoning"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"Actual answer"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=sse_body)

    transport = httpx.MockTransport(mock_handler)
    http_client = httpx.AsyncClient(transport=transport)

    config = ProviderConfig(
        name="test-deepseek",
        type="openai",
        api_key="sk-test",
        model="deepseek-r1",
    )
    client = OpenAIStreamClient(config=config, http_client=http_client)

    conv = ConversationManager()
    conv.add_user_message("Explain")

    events = [ev async for ev in client.stream(conv)]

    thinking_events = [e for e in events if isinstance(e, ThinkingDelta)]
    assert len(thinking_events) == 1
    assert thinking_events[0].thinking == "Step 1 reasoning"

    text_events = [e for e in events if isinstance(e, TextDelta)]
    assert len(text_events) == 1
    assert text_events[0].text == "Actual answer"

    await client.close()
    await http_client.aclose()


@pytest.mark.anyio
async def test_openai_stream_client_error_handling() -> None:
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(mock_handler)
    http_client = httpx.AsyncClient(transport=transport)

    config = ProviderConfig(
        name="test-openai",
        type="openai",
        api_key="sk-test",
        model="gpt-4o",
    )
    client = OpenAIStreamClient(config=config, http_client=http_client)

    conv = ConversationManager()
    conv.add_user_message("Hello")

    with pytest.raises(LLMStreamError, match="OpenAI API error \\(500\\)"):
        async for _ in client.stream(conv):
            pass

    await client.close()
    await http_client.aclose()


@pytest.mark.anyio
async def test_anthropic_stream_client() -> None:
    lines = [
        "event: message_start\n",
        'data: {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}\n\n',
        "event: content_block_start\n",
        'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}\n\n',
        "event: content_block_delta\n",
        'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Pondering"}}\n\n',
        "event: content_block_stop\n",
        'data: {"type": "content_block_stop", "index": 0}\n\n',
        "event: content_block_start\n",
        'data: {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}\n\n',
        "event: content_block_delta\n",
        'data: {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Anthropic response"}}\n\n',
        "event: content_block_stop\n",
        'data: {"type": "content_block_stop", "index": 1}\n\n',
        "event: message_stop\n",
        'data: {"type": "message_stop"}\n\n',
    ]
    sse_body = "".join(lines)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=sse_body)

    transport = httpx.MockTransport(mock_handler)
    http_client = httpx.AsyncClient(transport=transport)

    config = ProviderConfig(
        name="test-claude",
        type="anthropic",
        api_key="sk-ant",
        model="claude-3-5-sonnet-20241022",
        thinking=True,
    )
    client = AnthropicStreamClient(config=config, http_client=http_client)

    conv = ConversationManager()
    conv.add_user_message("Hello Claude")

    events = [ev async for ev in client.stream(conv)]

    thinking_events = [e for e in events if isinstance(e, ThinkingDelta)]
    assert len(thinking_events) == 1
    assert thinking_events[0].thinking == "Pondering"

    text_events = [e for e in events if isinstance(e, TextDelta)]
    assert len(text_events) == 1
    assert text_events[0].text == "Anthropic response"

    assert any(isinstance(e, ThinkingComplete) for e in events)
    assert any(isinstance(e, StreamEnd) for e in events)

    await client.close()
    await http_client.aclose()
