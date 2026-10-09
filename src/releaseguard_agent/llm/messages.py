from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ThinkingBlock:
    """Represents a discrete thinking / reasoning block within a message."""

    thinking: str
    signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"thinking": self.thinking}
        if self.signature:
            data["signature"] = self.signature
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ThinkingBlock":
        return cls(
            thinking=data.get("thinking", ""),
            signature=data.get("signature", ""),
        )


@dataclass
class ToolUseBlock:
    """Represents a tool call invocation issued by the model."""

    tool_use_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_use_id": self.tool_use_id,
            "tool_name": self.tool_name,
            "arguments": deepcopy(self.arguments),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolUseBlock":
        return cls(
            tool_use_id=data.get("tool_use_id", ""),
            tool_name=data.get("tool_name", ""),
            arguments=deepcopy(data.get("arguments", {})),
        )


@dataclass
class ToolResultBlock:
    """Represents the execution result of a previously invoked tool."""

    tool_use_id: str
    content: str
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_use_id": self.tool_use_id,
            "content": self.content,
            "is_error": self.is_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolResultBlock":
        return cls(
            tool_use_id=data.get("tool_use_id", ""),
            content=data.get("content", ""),
            is_error=bool(data.get("is_error", False)),
        )


@dataclass
class Message:
    """Dual-layer message structure supporting text, thinking blocks, and tool interactions."""

    role: Literal["user", "assistant", "system"]
    content: str = ""
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    tool_results: list[ToolResultBlock] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "thinking_blocks": [block.to_dict() for block in self.thinking_blocks],
            "tool_uses": [block.to_dict() for block in self.tool_uses],
            "tool_results": [block.to_dict() for block in self.tool_results],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        return cls(
            role=data["role"],
            content=data.get("content", ""),
            thinking_blocks=[
                ThinkingBlock.from_dict(b) for b in data.get("thinking_blocks", [])
            ],
            tool_uses=[ToolUseBlock.from_dict(b) for b in data.get("tool_uses", [])],
            tool_results=[
                ToolResultBlock.from_dict(b) for b in data.get("tool_results", [])
            ],
        )

    def clone(self) -> "Message":
        return deepcopy(self)
