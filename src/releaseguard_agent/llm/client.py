import copy
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


LLM_MESSAGE_ROLES = (
    "system",
    "user",
    "assistant",
    "tool",
)


@dataclass(frozen=True)
class LLMMessage:
    """Provider-neutral chat message for future LLM Agent workflows."""

    role: str
    content: str
    name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role not in LLM_MESSAGE_ROLES:
            raise ValueError(
                f"unsupported LLM message role: {self.role!r}"
            )
        if not self.content.strip():
            raise ValueError("LLM message content must not be empty")

        object.__setattr__(
            self,
            "metadata",
            copy.deepcopy(dict(self.metadata)),
        )

    @classmethod
    def system(
        cls,
        content: str,
        *,
        name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "LLMMessage":
        return cls(
            role="system",
            content=content,
            name=name,
            metadata=metadata or {},
        )

    @classmethod
    def user(
        cls,
        content: str,
        *,
        name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "LLMMessage":
        return cls(
            role="user",
            content=content,
            name=name,
            metadata=metadata or {},
        )

    @classmethod
    def assistant(
        cls,
        content: str,
        *,
        name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "LLMMessage":
        return cls(
            role="assistant",
            content=content,
            name=name,
            metadata=metadata or {},
        )

    @classmethod
    def tool(
        cls,
        content: str,
        *,
        name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "LLMMessage":
        return cls(
            role="tool",
            content=content,
            name=name,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
        }

        if self.name is not None:
            payload["name"] = self.name

        if self.metadata:
            payload["metadata"] = copy.deepcopy(dict(self.metadata))

        return payload


@dataclass(frozen=True)
class LLMResponse:
    """Provider-neutral LLM response used by Agent code."""

    content: str
    provider: str = "unknown"
    model: str | None = None
    finish_reason: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("LLM response content must not be empty")

        object.__setattr__(
            self,
            "usage",
            copy.deepcopy(dict(self.usage)),
        )
        object.__setattr__(
            self,
            "raw",
            copy.deepcopy(dict(self.raw)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "provider": self.provider,
            "model": self.model,
            "finish_reason": self.finish_reason,
            "usage": copy.deepcopy(dict(self.usage)),
            "raw": copy.deepcopy(dict(self.raw)),
        }


@dataclass(frozen=True)
class LLMCall:
    """Recorded LLM call for tests, traces, and future observability."""

    messages: tuple[LLMMessage, ...]
    model: str | None = None
    temperature: float | None = None
    response_format: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "messages",
            tuple(
                LLMMessage(
                    role=message.role,
                    content=message.content,
                    name=message.name,
                    metadata=message.metadata,
                )
                for message in self.messages
            ),
        )
        object.__setattr__(
            self,
            "metadata",
            copy.deepcopy(dict(self.metadata)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": [
                message.to_dict()
                for message in self.messages
            ],
            "model": self.model,
            "temperature": self.temperature,
            "response_format": self.response_format,
            "metadata": copy.deepcopy(dict(self.metadata)),
        }


@runtime_checkable
class LLMClient(Protocol):
    """Protocol implemented by fake and real LLM clients."""

    def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        response_format: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> LLMResponse:
        """Return one model response for the supplied messages."""
