import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from releaseguard_agent.config.provider import ProviderConfig
from releaseguard_agent.llm.client import LLMStreamError, StreamLLMClient
from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.events import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)


class AnthropicStreamClient(StreamLLMClient):
    """Anthropic protocol streaming client with thinking block isolation."""

    def __init__(
        self,
        config: ProviderConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=60.0)

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        base_url = (self.config.base_url or "https://api.anthropic.com/v1").rstrip("/")
        url = f"{base_url}/messages"

        # Transform messages for Anthropic (user and assistant only)
        messages_payload: list[dict[str, Any]] = []
        for msg in conversation.get_messages():
            role = "user" if msg.role == "user" else "assistant"
            messages_payload.append({"role": role, "content": msg.content})

        payload: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": 4096,
            "messages": messages_payload,
            "stream": True,
        }
        if system.strip():
            payload["system"] = system.strip()
        if self.config.thinking:
            payload["thinking"] = {"type": "enabled", "budget_tokens": 2048}
        if tools:
            payload["tools"] = tools

        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        active_blocks: dict[int, dict[str, Any]] = {}
        usage_accumulator: dict[str, int] = {}

        try:
            req = self._client.build_request("POST", url, headers=headers, json=payload)
            response = await self._client.send(req, stream=True)
            if response.status_code >= 400:
                body = await response.aread()
                await response.aclose()
                raise LLMStreamError(
                    f"Anthropic API error ({response.status_code}): {body.decode(errors='replace')}"
                )

            current_event_type = ""

            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue

                if line.startswith("event:"):
                    current_event_type = line[len("event:") :].strip()
                    continue

                if not line.startswith("data:"):
                    continue

                data_str = line[len("data:") :].strip()
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                if current_event_type == "error":
                    error_msg = data.get("error", {}).get(
                        "message", "Unknown Anthropic error"
                    )
                    raise LLMStreamError(f"Anthropic error: {error_msg}")

                elif current_event_type == "message_start":
                    msg_obj = data.get("message", {})
                    if "usage" in msg_obj:
                        usage_accumulator.update(msg_obj["usage"])

                elif current_event_type == "content_block_start":
                    idx = data.get("index", 0)
                    cb = data.get("content_block", {})
                    block_type = cb.get("type", "text")
                    active_blocks[idx] = {
                        "type": block_type,
                        "id": cb.get("id", ""),
                        "name": cb.get("name", ""),
                        "arguments": "",
                    }
                    if block_type == "tool_use":
                        yield ToolCallStart(
                            tool_id=active_blocks[idx]["id"],
                            tool_name=active_blocks[idx]["name"],
                        )

                elif current_event_type == "content_block_delta":
                    idx = data.get("index", 0)
                    delta = data.get("delta", {})
                    delta_type = delta.get("type", "")

                    if delta_type == "text_delta":
                        yield TextDelta(text=delta.get("text", ""))
                    elif delta_type == "thinking_delta":
                        yield ThinkingDelta(thinking=delta.get("thinking", ""))
                    elif delta_type == "input_json_delta":
                        partial_json = delta.get("partial_json", "")
                        if idx in active_blocks:
                            active_blocks[idx]["arguments"] += partial_json
                            yield ToolCallDelta(
                                tool_id=active_blocks[idx]["id"],
                                arguments_delta=partial_json,
                            )

                elif current_event_type == "content_block_stop":
                    idx = data.get("index", 0)
                    if idx in active_blocks:
                        info = active_blocks.pop(idx)
                        if info["type"] == "thinking":
                            yield ThinkingComplete()
                        elif info["type"] == "tool_use":
                            try:
                                parsed = (
                                    json.loads(info["arguments"])
                                    if info["arguments"]
                                    else {}
                                )
                            except json.JSONDecodeError:
                                parsed = {"raw": info["arguments"]}
                            yield ToolCallComplete(
                                tool_id=info["id"],
                                tool_name=info["name"],
                                arguments=parsed,
                            )

                elif current_event_type == "message_delta":
                    if "usage" in data:
                        usage_accumulator.update(data["usage"])

                elif current_event_type == "message_stop":
                    yield StreamEnd(
                        usage=usage_accumulator if usage_accumulator else None
                    )

            await response.aclose()
            yield StreamEnd(usage=usage_accumulator if usage_accumulator else None)

        except httpx.HTTPError as exc:
            raise LLMStreamError(f"HTTP stream error: {exc}") from exc

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
