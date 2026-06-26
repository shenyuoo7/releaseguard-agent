import copy
from collections import deque
from typing import Iterable, Mapping, Sequence

from releaseguard_agent.llm.client import (
    LLMCall,
    LLMMessage,
    LLMResponse,
)


class MissingFakeLLMResponseError(RuntimeError):
    """Raised when FakeLLMClient has no queued response for a call."""


class FakeLLMClient:
    """Deterministic LLM client for Agent workflow tests.

    This client never calls a real provider. Tests can queue responses and
    inspect recorded calls to verify prompt construction and model options.
    """

    def __init__(
        self,
        responses: Iterable[LLMResponse | str] | None = None,
    ) -> None:
        self._responses: deque[LLMResponse] = deque()

        for response in responses or ():
            self.add_response(response)

        self.calls: list[LLMCall] = []

    @property
    def queued_response_count(self) -> int:
        return len(self._responses)

    def add_response(
        self,
        response: LLMResponse | str,
    ) -> None:
        self._responses.append(_coerce_response(response))

    def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        response_format: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> LLMResponse:
        call = LLMCall(
            messages=tuple(messages),
            model=model,
            temperature=temperature,
            response_format=response_format,
            metadata=metadata or {},
        )
        self.calls.append(call)

        if not self._responses:
            raise MissingFakeLLMResponseError(
                "FakeLLMClient has no queued response for this call"
            )

        return _copy_response(self._responses.popleft())


def _coerce_response(
    response: LLMResponse | str,
) -> LLMResponse:
    if isinstance(response, LLMResponse):
        return _copy_response(response)

    return LLMResponse(
        content=response,
        provider="fake",
        model="fake-model",
        finish_reason="stop",
    )


def _copy_response(
    response: LLMResponse,
) -> LLMResponse:
    return LLMResponse(
        content=response.content,
        provider=response.provider,
        model=response.model,
        finish_reason=response.finish_reason,
        usage=copy.deepcopy(dict(response.usage)),
        raw=copy.deepcopy(dict(response.raw)),
    )
