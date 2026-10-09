import asyncio
from collections.abc import AsyncIterator
from typing import Any, Sequence

from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.events import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
)


class FakeStreamClient:
    """Offline, deterministic streaming LLM client for tests."""

    def __init__(
        self,
        events: Sequence[StreamEvent] | None = None,
        text_chunks: Sequence[str] | None = None,
        thinking_chunks: Sequence[str] | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self.delay_s = delay_s
        self.calls: list[dict[str, Any]] = []
        self._preset_events: list[StreamEvent] = []

        if events is not None:
            self._preset_events.extend(events)
        else:
            if thinking_chunks:
                for chunk in thinking_chunks:
                    self._preset_events.append(ThinkingDelta(thinking=chunk))
                self._preset_events.append(ThinkingComplete())
            if text_chunks:
                for chunk in text_chunks:
                    self._preset_events.append(TextDelta(text=chunk))
            self._preset_events.append(StreamEnd())

        self.closed: bool = False

    def set_events(self, events: Sequence[StreamEvent]) -> None:
        self._preset_events = list(events)

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(
            {
                "conversation": conversation.clone(),
                "system": system,
                "tools": tools,
            }
        )

        for event in self._preset_events:
            if self.delay_s > 0:
                await asyncio.sleep(self.delay_s)
            yield event

    async def close(self) -> None:
        self.closed = True


FakeStreamLLMClient = FakeStreamClient
