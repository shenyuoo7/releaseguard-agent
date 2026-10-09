from dataclasses import dataclass, field
from typing import Any, Mapping

from releaseguard_agent.tools.base import BaseTool


@dataclass
class ToolCallItem:
    """Represents a single tool invocation request in the batch."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolBatch:
    """A batch of tool calls grouped by concurrency safety."""

    is_concurrent: bool
    calls: list[ToolCallItem] = field(default_factory=list)


def partition_tool_calls(
    calls: list[ToolCallItem],
    tool_lookup: Mapping[str, BaseTool],
) -> list[ToolBatch]:
    """Partition a sequence of tool calls into concurrent read batches and serial write batches.

    Consecutive read-only / concurrency-safe tools are grouped together into a single
    concurrent batch to be executed in parallel via asyncio.gather.
    Any mutating or non-concurrency-safe tool is placed into its own serial batch.
    """
    batches: list[ToolBatch] = []

    for call in calls:
        tool = tool_lookup.get(call.name)
        safe = bool(tool and tool.is_concurrency_safe)

        if safe and batches and batches[-1].is_concurrent:
            batches[-1].calls.append(call)
        else:
            batches.append(ToolBatch(is_concurrent=safe, calls=[call]))

    return batches
