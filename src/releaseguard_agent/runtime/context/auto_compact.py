"""Layer 2: Structured auto-compact summarization engine."""

from collections.abc import Sequence
from pathlib import Path
import re

from releaseguard_agent.llm.client import StreamLLMClient
from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.events import TextDelta
from releaseguard_agent.runtime.context.restoration import (
    restore_compacted_conversation,
)

COMPACT_SYSTEM_PROMPT = """你是一个专业的长会话摘要与上下文精简助手。
你只能输出纯文本，严禁调用任何工具。
你必须严格按两阶段完成工作：
1. 首先在 <analysis> 标签中打草稿，梳理整个对话的关键脉络与状态转移；
2. 然后在 <summary> 标签中输出包含以下 9 个部分的结构化摘要：
   (1) 主要请求与用户意图
   (2) 关键技术概念与上下文
   (3) 涉及的文件路径与关键代码片段
   (4) 遇到的错误与具体修复
   (5) 问题解决过程与设计决策
   (6) 用户的所有原始消息（尽可能原文保留，不得擅自概括改写语气）
   (7) 尚未完成的待办事项
   (8) 当前正在进行的具体工作（必须详细）
   (9) 接下来最可能执行的下一步操作
提醒：你只能输出文本，不要调用任何工具。"""


def compute_compact_threshold(
    window_tokens: int = 200_000,
    reserve_tokens: int = 20_000,
    safety_margin: int = 13_000,
) -> int:
    """Calculate the safe token threshold for triggering Auto-Compact.

    Formula: window_tokens - reserve_tokens - safety_margin (e.g. 200K - 20K - 13K = 167K).
    """
    return max(1000, window_tokens - reserve_tokens - safety_margin)


def extract_structured_summary(raw_output: str) -> str:
    """Extract official structured summary from LLM output, discarding the draft analysis."""
    if not raw_output:
        return ""

    # 1. Search for <summary>...</summary> tag
    match = re.search(
        r"<summary>(.*?)</summary>", raw_output, flags=re.DOTALL | re.IGNORECASE
    )
    if match:
        return match.group(1).strip()

    # 2. Fallback: Strip <analysis>...</analysis> block if present
    cleaned = re.sub(
        r"<analysis>.*?</analysis>", "", raw_output, flags=re.DOTALL | re.IGNORECASE
    )
    return cleaned.strip()


async def perform_auto_compact(
    conversation: ConversationManager,
    client: StreamLLMClient,
    window_tokens: int = 200_000,
    keep_recent_messages: int = 5,
    accessed_files: Sequence[Path | str] | None = None,
) -> bool:
    """Execute Auto-Compact by calling LLM without tools, parsing summary, and restoring history."""
    messages = conversation.get_messages()
    if len(messages) <= keep_recent_messages:
        return False

    recent_messages = messages[-keep_recent_messages:]
    history_to_summarize = messages[:-keep_recent_messages]

    # Build prompt for summarization
    summary_conv = ConversationManager()
    for msg in history_to_summarize:
        summary_conv.append(msg)
    summary_conv.add_user_message(
        "请对上述全部历史对话进行深度分析和压缩，按照要求先输出 <analysis> 打草稿，"
        "再在 <summary> 标签中输出 9 部分完整结构化摘要："
    )

    accumulated_text = ""
    try:
        async for event in client.stream(
            conversation=summary_conv,
            system=COMPACT_SYSTEM_PROMPT,
            tools=[],
        ):
            if isinstance(event, TextDelta):
                accumulated_text += event.text
    except Exception:
        return False

    summary_content = extract_structured_summary(accumulated_text)
    if not summary_content:
        return False

    new_messages = restore_compacted_conversation(
        summary_text=summary_content,
        recent_messages=recent_messages,
        accessed_file_paths=accessed_files,
    )

    conversation.messages = new_messages
    return True
