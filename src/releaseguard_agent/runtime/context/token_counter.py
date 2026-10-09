"""Token counting utilities using tiktoken."""

from collections.abc import Sequence
from typing import Any
import tiktoken

from releaseguard_agent.llm.messages import Message

_ENCODING = None


def get_default_encoding() -> tiktoken.Encoding:
    """Lazily load and cache the cl100k_base tokenizer encoding."""
    global _ENCODING
    if _ENCODING is None:
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return _ENCODING


def count_text_tokens(text: str) -> int:
    """Return exact token count for the given text string."""
    if not text:
        return 0
    encoding = get_default_encoding()
    return len(encoding.encode(text, disallowed_special=()))


def count_message_tokens(msg: Any) -> int:
    """Calculate approximate tokens for a single message, including role and tool blocks."""
    # Base framing overhead per message in chat completions (~4 tokens)
    tokens = 4

    if isinstance(msg, Message):
        tokens += count_text_tokens(msg.role)
        tokens += count_text_tokens(msg.content)

        for tb in msg.thinking_blocks:
            tokens += count_text_tokens(tb.thinking)

        for tu in msg.tool_uses:
            tokens += count_text_tokens(tu.tool_name)
            tokens += count_text_tokens(str(tu.arguments))

        for tr in msg.tool_results:
            tokens += count_text_tokens(tr.tool_use_id)
            tokens += count_text_tokens(tr.content)
    elif isinstance(msg, dict):
        role = str(msg.get("role", ""))
        content = str(msg.get("content", ""))
        tokens += count_text_tokens(role)
        tokens += count_text_tokens(content)
    else:
        tokens += count_text_tokens(str(getattr(msg, "content", msg)))

    return tokens


def estimate_context_tokens(
    messages: Sequence[Any],
    system_prompt: str = "",
) -> int:
    """Calculate total token count for a list of messages and an optional system prompt."""
    total = 3  # Conversation start overhead
    if system_prompt:
        total += 4 + count_text_tokens(system_prompt)

    for msg in messages:
        total += count_message_tokens(msg)

    return total
