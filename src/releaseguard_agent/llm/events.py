from dataclasses import dataclass
from typing import Any, Union


@dataclass(frozen=True)
class TextDelta:
    """Incremental text chunk from LLM response."""

    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    """Incremental reasoning / thinking chunk from reasoning-capable LLM."""

    thinking: str


@dataclass(frozen=True)
class ThinkingComplete:
    """Signals that model reasoning / thinking phase has finished."""

    pass


@dataclass(frozen=True)
class ToolCallStart:
    """Signals start of a tool invocation."""

    tool_id: str
    tool_name: str


@dataclass(frozen=True)
class ToolCallDelta:
    """Incremental argument delta for an in-flight tool call."""

    tool_id: str
    arguments_delta: str


@dataclass(frozen=True)
class ToolCallComplete:
    """Signals completion of tool call arguments parsing."""

    tool_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class StreamEnd:
    """Signals completion of the entire stream response."""

    usage: dict[str, int] | None = None


StreamEvent = Union[
    TextDelta,
    ThinkingDelta,
    ThinkingComplete,
    ToolCallStart,
    ToolCallDelta,
    ToolCallComplete,
    StreamEnd,
]
