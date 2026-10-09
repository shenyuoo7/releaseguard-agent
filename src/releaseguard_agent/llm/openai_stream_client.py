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


class OpenAIStreamClient(StreamLLMClient):
    """OpenAI protocol streaming client with thinking block isolation."""

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
        base_url = (self.config.base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{base_url}/chat/completions"

        messages_payload: list[dict[str, Any]] = []
        if system.strip():
            messages_payload.append({"role": "system", "content": system.strip()})

        for msg in conversation.get_messages():
            messages_payload.append({"role": msg.role, "content": msg.content})

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages_payload,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

        # Parsing state
        in_think_tag = False
        saw_reasoning_content = False
        tool_calls_accumulator: dict[int, dict[str, Any]] = {}

        try:
            req = self._client.build_request("POST", url, headers=headers, json=payload)
            response = await self._client.send(req, stream=True)
            if response.status_code >= 400:
                body = await response.aread()
                await response.aclose()
                raise LLMStreamError(
                    f"OpenAI API error ({response.status_code}): {body.decode(errors='replace')}"
                )

            async for line in response.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue

                data_str = line[len("data:") :].strip()
                if data_str == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                usage = chunk.get("usage")
                choices = chunk.get("choices") or []
                if not choices:
                    if usage:
                        yield StreamEnd(usage=usage)
                    continue

                delta = choices[0].get("delta", {})

                # 1. Native reasoning_content (DeepSeek API, etc.)
                reasoning_chunk = delta.get("reasoning_content")
                if reasoning_chunk:
                    saw_reasoning_content = True
                    yield ThinkingDelta(thinking=reasoning_chunk)

                # 2. Regular content with possible <think> tags
                content_chunk = delta.get("content")
                if content_chunk:
                    if saw_reasoning_content and not in_think_tag:
                        # Transition from reasoning_content to content
                        yield ThinkingComplete()
                        saw_reasoning_content = False

                    if "<think>" in content_chunk and not in_think_tag:
                        before, _, after = content_chunk.partition("<think>")
                        if before:
                            yield TextDelta(text=before)
                        in_think_tag = True
                        content_chunk = after

                    if in_think_tag:
                        if "</think>" in content_chunk:
                            think_part, _, after = content_chunk.partition("</think>")
                            if think_part:
                                yield ThinkingDelta(thinking=think_part)
                            yield ThinkingComplete()
                            in_think_tag = False
                            if after:
                                yield TextDelta(text=after)
                        else:
                            yield ThinkingDelta(thinking=content_chunk)
                    else:
                        yield TextDelta(text=content_chunk)

                # 3. Tool call streaming
                tc_deltas = delta.get("tool_calls")
                if tc_deltas:
                    for tc in tc_deltas:
                        idx = tc.get("index", 0)
                        if idx not in tool_calls_accumulator:
                            tool_calls_accumulator[idx] = {
                                "id": tc.get("id", f"call_{idx}"),
                                "name": tc.get("function", {}).get("name", ""),
                                "arguments": "",
                            }
                            yield ToolCallStart(
                                tool_id=tool_calls_accumulator[idx]["id"],
                                tool_name=tool_calls_accumulator[idx]["name"],
                            )

                        fn_delta = tc.get("function", {})
                        if (
                            fn_delta.get("name")
                            and not tool_calls_accumulator[idx]["name"]
                        ):
                            tool_calls_accumulator[idx]["name"] = fn_delta["name"]

                        arg_delta = fn_delta.get("arguments", "")
                        if arg_delta:
                            tool_calls_accumulator[idx]["arguments"] += arg_delta
                            yield ToolCallDelta(
                                tool_id=tool_calls_accumulator[idx]["id"],
                                arguments_delta=arg_delta,
                            )

            await response.aclose()

            # Finalize thinking if ended while still inside think tag or reasoning
            if in_think_tag or saw_reasoning_content:
                yield ThinkingComplete()

            # Finalize completed tool calls
            for item in tool_calls_accumulator.values():
                try:
                    parsed_args = (
                        json.loads(item["arguments"]) if item["arguments"] else {}
                    )
                except json.JSONDecodeError:
                    parsed_args = {"raw": item["arguments"]}
                yield ToolCallComplete(
                    tool_id=item["id"],
                    tool_name=item["name"],
                    arguments=parsed_args,
                )

            yield StreamEnd()

        except httpx.HTTPError as exc:
            raise LLMStreamError(f"HTTP stream error: {exc}") from exc

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
