from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from releaseguard_agent.llm.messages import ToolResultBlock


@dataclass(frozen=True)
class ToolResult:
    """Execution output from a tool invocation."""

    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_tool_result_block(self, tool_use_id: str) -> ToolResultBlock:
        return ToolResultBlock(
            tool_use_id=tool_use_id,
            content=self.content,
            is_error=self.is_error,
        )


@dataclass
class ToolContext:
    """Runtime context for tool execution, ensuring explicit cwd."""

    cwd: Path = field(default_factory=Path.cwd)
    session_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def resolve_path(self, target: str | Path) -> Path:
        """Resolve a relative or absolute path against context.cwd."""
        p = Path(target)
        if p.is_absolute():
            return p
        return (self.cwd / p).resolve()


class BaseTool(ABC):
    """Abstract base class for all callable tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for the tool."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human and LLM-readable description of tool capabilities."""
        ...

    @property
    @abstractmethod
    def parameters_schema(self) -> dict[str, Any]:
        """JSON Schema defining tool arguments."""
        ...

    @property
    def is_read_only(self) -> bool:
        """Indicates if the tool has zero mutating side-effects."""
        return False

    @property
    def is_concurrency_safe(self) -> bool:
        """Indicates if tool invocations can safely execute in parallel."""
        return self.is_read_only

    @property
    def category(self) -> str:
        """Category tag for grouping or permission rules."""
        return "general"

    def validate_input(self, arguments: dict[str, Any]) -> str | None:
        """Validate input arguments. Returns an error message string or None."""
        return None

    @abstractmethod
    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Execute the tool with given arguments and runtime context."""
        ...
