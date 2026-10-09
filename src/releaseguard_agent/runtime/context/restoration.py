"""Context restoration after compaction rebuilding summary, boundary, and key files."""

from pathlib import Path
from typing import Sequence

from releaseguard_agent.llm.messages import Message

BOUNDARY_MESSAGE = (
    "[边界消息] 上面是之前对话的摘要。如果需要文件的具体内容，"
    "请用 read_file 重新读取，不要根据摘要猜测代码细节。"
)


def restore_compacted_conversation(
    summary_text: str,
    recent_messages: Sequence[Message],
    accessed_file_paths: Sequence[Path | str] | None = None,
    max_files: int = 5,
    max_file_tokens: int = 5000,
) -> list[Message]:
    """Reconstruct a compact, informative conversation message history after compaction.

    Structure:
    1. Structured conversation summary
    2. Boundary notice preventing hallucinations
    3. Re-attached recent file contents (up to max_files, each capped at max_file_tokens)
    4. Recent un-compacted user/assistant messages for continuous dialogue flow
    """
    new_messages: list[Message] = []

    # 1. Summary block
    new_messages.append(
        Message(
            role="user",
            content=f"# 历史对话结构化摘要\n\n{summary_text.strip()}",
        )
    )

    # 2. Boundary message
    new_messages.append(
        Message(
            role="user",
            content=BOUNDARY_MESSAGE,
        )
    )

    # 3. Restore accessed key files
    if accessed_file_paths:
        restored_count = 0
        char_cap = max_file_tokens * 4

        for p_raw in accessed_file_paths:
            if restored_count >= max_files:
                break
            p = Path(p_raw)
            if p.is_file():
                try:
                    raw_content = p.read_text(encoding="utf-8")
                    truncated_content = raw_content[:char_cap]
                    suffix = (
                        "\n... [内容已截断，使用 read_file 查看完整内容]"
                        if len(raw_content) > char_cap
                        else ""
                    )
                    file_msg = (
                        f"# 关键上下文恢复: {p.as_posix()}\n"
                        f"```\n{truncated_content}{suffix}\n```"
                    )
                    new_messages.append(Message(role="user", content=file_msg))
                    restored_count += 1
                except Exception:
                    continue

    # 4. Re-append recent messages
    for msg in recent_messages:
        new_messages.append(msg.clone())

    return new_messages
