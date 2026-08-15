"""Deterministic safety checks for registered runtime tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any

from releaseguard_agent.api.path_policy import (
    ProjectPathNotAllowedError,
    ProjectPathPolicy,
)

from .models import RunBudget

if TYPE_CHECKING:
    from .tools import _CompletedToolResult, ToolCall, ToolSpec


class GuardrailDecision(str, Enum):
    """The closed set of outcomes allowed before tool invocation."""

    ALLOW = "ALLOW"
    REQUIRE_HITL = "REQUIRE_HITL"
    RETRY = "RETRY"
    BLOCK = "BLOCK"
    QUARANTINE = "QUARANTINE"


@dataclass
class ToolExecutionContext:
    """Per-run mutable counters and retained runtime-only tool results."""

    budget: RunBudget
    offline_mode: bool = True
    tool_call_count: int = 0
    retry_count: int = 0
    max_output_bytes: int = 1_000_000
    approved_scopes: frozenset[str] = field(default_factory=frozenset)
    completed_results: dict[tuple[str, int, str], "_CompletedToolResult"] = field(
        default_factory=dict
    )
    references: dict[str, object] = field(default_factory=dict)
    ambiguous_fingerprints: set[tuple[str, str, str]] = field(default_factory=set)
    idempotency_lock: Any = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.tool_call_count < 0:
            raise ValueError("tool_call_count must not be negative")
        if self.retry_count < 0:
            raise ValueError("retry_count must not be negative")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be greater than zero")


class GuardrailEngine:
    """Fail closed before a registered handler can inspect unsafe input."""

    def check(
        self,
        call: "ToolCall",
        spec: "ToolSpec",
        context: ToolExecutionContext,
        *,
        include_tool_call_budget: bool = True,
    ) -> GuardrailDecision:
        return self._evaluate(call, spec, context, include_tool_call_budget)[0]

    def error_type(
        self,
        call: "ToolCall",
        spec: "ToolSpec",
        context: ToolExecutionContext,
        *,
        include_tool_call_budget: bool = True,
    ) -> str | None:
        return self._evaluate(call, spec, context, include_tool_call_budget)[1]

    @staticmethod
    def _evaluate(
        call: "ToolCall",
        spec: "ToolSpec",
        context: ToolExecutionContext,
        include_tool_call_budget: bool,
    ) -> tuple[GuardrailDecision, str | None]:
        if (spec.side_effect == "network") != (spec.network_policy == "network"):
            return GuardrailDecision.BLOCK, "capability_inconsistent"
        if call.step_index >= context.budget.max_steps:
            return GuardrailDecision.BLOCK, "step_budget_exceeded"
        if context.retry_count > min(spec.max_retries, context.budget.max_retries):
            return GuardrailDecision.BLOCK, "retry_budget_exceeded"
        if (
            include_tool_call_budget
            and context.tool_call_count + spec.budget_cost
            > context.budget.max_tool_calls
        ):
            return GuardrailDecision.BLOCK, "tool_call_budget_exceeded"
        if _contains_dotenv(call.args):
            return GuardrailDecision.QUARANTINE, "sensitive_path_argument"
        if not _paths_are_allowed(call.args, spec.allowed_roots):
            return GuardrailDecision.BLOCK, "path_not_allowed"
        if context.offline_mode and spec.network_policy != "offline":
            return GuardrailDecision.BLOCK, "network_disabled"
        approval_required = (
            spec.side_effect != "read_only"
            or spec.network_policy == "network"
            or spec.required_approval_scope is not None
        )
        if approval_required and (
            spec.required_approval_scope is None
            or spec.required_approval_scope not in context.approved_scopes
        ):
            return GuardrailDecision.REQUIRE_HITL, "approval_required"
        return GuardrailDecision.ALLOW, None


def _contains_dotenv(value: Any) -> bool:
    if isinstance(value, str):
        return any(part.lower() == ".env" for part in Path(value).parts)
    if isinstance(value, dict):
        return any(_contains_dotenv(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_dotenv(item) for item in value)
    return False


def _paths_are_allowed(args: dict[str, Any], allowed_roots: tuple[Path, ...]) -> bool:
    path_values = [
        value
        for key, value in args.items()
        if isinstance(value, str) and (key == "project_path" or key.endswith("_path"))
    ]
    if not path_values:
        return True
    if not allowed_roots:
        return False
    policy = ProjectPathPolicy(allowed_roots)
    try:
        for value in path_values:
            policy.resolve_allowed(value)
    except (OSError, ProjectPathNotAllowedError, ValueError):
        return False
    return True
