"""Layer 1: Tool result budget enforcement and decision-freezing algorithm."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_SINGLE_RESULT_THRESHOLD = 50_000  # 50,000 characters
DEFAULT_AGGREGATE_RESULT_THRESHOLD = 200_000  # 200,000 characters
PREVIEW_LIMIT = 2_000  # 2,000 characters


@dataclass
class ContentReplacementState:
    """State tracker for tool result persistence ensuring prompt-cache decision freezing."""

    seen_ids: set[str] = field(default_factory=set)
    replacements: dict[str, str] = field(default_factory=dict)


def _persist_and_record(
    result: Any,
    state: ContentReplacementState,
    storage_dir: Path,
) -> None:
    """Write large tool output to disk and record its 2KB preview replacement block."""
    tool_use_id = getattr(result, "tool_use_id", "")
    content = getattr(result, "content", "")

    file_path = storage_dir / f"{tool_use_id}.txt"
    if not file_path.is_file():
        try:
            file_path.write_text(content, encoding="utf-8")
        except Exception:
            # If disk write fails, fall back to preserving content in-memory
            return

    preview = content[:PREVIEW_LIMIT]
    normalized_path = file_path.resolve().as_posix()
    replacement = (
        f'<persisted-output tool_use_id="{tool_use_id}" path="{normalized_path}">\n'
        f"[输出过大，完整内容已保存至 {normalized_path}，以下为前 2KB 预览：]\n"
        f"{preview}\n"
        f"</persisted-output>"
    )
    state.replacements[tool_use_id] = replacement


def apply_tool_result_budget(
    tool_results: list[Any],
    state: ContentReplacementState,
    storage_dir: Path,
    single_threshold: int = DEFAULT_SINGLE_RESULT_THRESHOLD,
    aggregate_threshold: int = DEFAULT_AGGREGATE_RESULT_THRESHOLD,
) -> None:
    """Enforce character budgets on tool results and replay frozen decisions.

    Guarantees that decisions made in previous turns are never reversed,
    preserving prompt cache prefix byte stability.
    """
    storage_dir.mkdir(parents=True, exist_ok=True)

    # 1. Identify newly arrived tool results
    new_results = [
        r for r in tool_results if getattr(r, "tool_use_id", "") not in state.seen_ids
    ]

    # Check single-item threshold for new results
    for r in new_results:
        t_id = getattr(r, "tool_use_id", "")
        content = getattr(r, "content", "")
        state.seen_ids.add(t_id)

        if len(content) > single_threshold:
            _persist_and_record(r, state, storage_dir)

    # 2. Check aggregate threshold for the current turn
    current_total = sum(
        len(
            state.replacements.get(
                getattr(r, "tool_use_id", ""), getattr(r, "content", "")
            )
        )
        for r in tool_results
    )

    if current_total > aggregate_threshold:
        # Candidate pool: only newly arrived results that were not already persisted
        candidates = [
            r
            for r in new_results
            if getattr(r, "tool_use_id", "") not in state.replacements
        ]
        # Sort descending by content length to persist largest outputs first
        candidates.sort(key=lambda x: len(getattr(x, "content", "")), reverse=True)

        for r in candidates:
            t_id = getattr(r, "tool_use_id", "")
            orig_len = len(getattr(r, "content", ""))
            _persist_and_record(r, state, storage_dir)
            new_len = len(state.replacements.get(t_id, ""))
            current_total -= orig_len - new_len

            if current_total <= aggregate_threshold:
                break

    # 3. Apply frozen replacements in-place
    for r in tool_results:
        t_id = getattr(r, "tool_use_id", "")
        if t_id in state.replacements:
            if hasattr(r, "content"):
                r.content = state.replacements[t_id]
            elif isinstance(r, dict) and "content" in r:
                r["content"] = state.replacements[t_id]
