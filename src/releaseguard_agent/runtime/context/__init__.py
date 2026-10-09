"""Context management and token budgeting package."""

from releaseguard_agent.runtime.context.auto_compact import (
    COMPACT_SYSTEM_PROMPT,
    compute_compact_threshold,
    extract_structured_summary,
    perform_auto_compact,
)
from releaseguard_agent.runtime.context.restoration import (
    BOUNDARY_MESSAGE,
    restore_compacted_conversation,
)
from releaseguard_agent.runtime.context.result_budget import (
    DEFAULT_AGGREGATE_RESULT_THRESHOLD,
    DEFAULT_SINGLE_RESULT_THRESHOLD,
    ContentReplacementState,
    apply_tool_result_budget,
)
from releaseguard_agent.runtime.context.token_counter import (
    count_message_tokens,
    count_text_tokens,
    estimate_context_tokens,
)

__all__ = [
    "BOUNDARY_MESSAGE",
    "COMPACT_SYSTEM_PROMPT",
    "ContentReplacementState",
    "DEFAULT_AGGREGATE_RESULT_THRESHOLD",
    "DEFAULT_SINGLE_RESULT_THRESHOLD",
    "apply_tool_result_budget",
    "compute_compact_threshold",
    "count_message_tokens",
    "count_text_tokens",
    "estimate_context_tokens",
    "extract_structured_summary",
    "perform_auto_compact",
    "restore_compacted_conversation",
]
