from copy import deepcopy
from typing import Sequence

from releaseguard_agent.llm.messages import (
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class ConversationManager:
    """Manages chat conversation message history across multiple turns."""

    def __init__(self, messages: Sequence[Message] | None = None) -> None:
        self.messages: list[Message] = list(messages) if messages else []

    def add_user_message(self, text: str) -> Message:
        msg = Message(role="user", content=text)
        self.messages.append(msg)
        return msg

    def add_assistant_message(
        self,
        text: str = "",
        thinking: list[ThinkingBlock] | None = None,
        tool_uses: list[ToolUseBlock] | None = None,
        tool_results: list[ToolResultBlock] | None = None,
    ) -> Message:
        msg = Message(
            role="assistant",
            content=text,
            thinking_blocks=thinking or [],
            tool_uses=tool_uses or [],
            tool_results=tool_results or [],
        )
        self.messages.append(msg)
        return msg

    def append(self, message: Message) -> None:
        self.messages.append(message)

    def clear(self) -> None:
        self.messages.clear()

    def get_messages(self) -> list[Message]:
        return list(self.messages)

    def clone(self) -> "ConversationManager":
        return ConversationManager(deepcopy(self.messages))

    def estimate_tokens(self) -> int:
        """Rough heuristic token estimator (~4 chars per token) for UI display."""
        char_count = sum(
            len(m.content)
            + sum(len(t.thinking) for t in m.thinking_blocks)
            + sum(len(str(u.arguments)) + len(u.tool_name) for u in m.tool_uses)
            + sum(len(r.content) for r in m.tool_results)
            for m in self.messages
        )
        return max(0, char_count // 4)

    def to_dict_list(self) -> list[dict]:
        return [m.to_dict() for m in self.messages]
