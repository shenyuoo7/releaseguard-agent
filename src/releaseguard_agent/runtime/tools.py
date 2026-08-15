"""Versioned registered tool contracts and deterministic execution."""

from __future__ import annotations

import copy
import json
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Literal
from types import MappingProxyType

from releaseguard_agent.observability.execution_trace import _redact

from .guardrails import GuardrailDecision, GuardrailEngine, ToolExecutionContext
from .models import canonical_json, sha256_json


Schema = Mapping[str, type | tuple[type, ...]]
ToolHandler = Callable[[dict[str, Any], ToolExecutionContext], Mapping[str, Any]]
ToolPreparer = Callable[
    [dict[str, Any], ToolExecutionContext], "ToolPreparationResult"
]
ToolStatus = Literal["completed", "blocked", "retry", "quarantined", "error"]


class SensitiveToolArgumentError(ValueError):
    """Raised before a secret-bearing tool argument can be serialized or hashed."""


_SENSITIVE_ARGUMENT_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_key",
        "authorization",
        "bearer",
        "credential",
        "credentials",
        "client_secret",
        "refresh_token",
        "auth_token",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
_PROVIDER_CREDENTIAL_KEY = re.compile(
    r"^(?:"
    r"github|gitlab|openai|anthropic|azure|aws|gcp|google|slack|"
    r"stripe|huggingface"
    r")_(?:"
    r"pat|token|access_token|api_key|client_secret|refresh_token|"
    r"auth_token|secret"
    r")$"
)
_SENSITIVE_SCALAR_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bgh[opsru]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?key|password|private[_-]?key|secret|token)\s*[:=]\s*\S+"
    ),
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    input_schema: Schema
    output_schema: Schema
    side_effect: Literal[
        "read_only",
        "write",
        "control_plane_write",
        "project_write",
        "network",
    ]
    allowed_roots: tuple[Path, ...]
    network_policy: Literal["offline", "network"]
    timeout_ms: int
    max_retries: int
    budget_cost: int
    required_approval_scope: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("tool name must be non-empty")
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("tool version must be non-empty")
        _validate_schema(self.input_schema, "input_schema")
        _validate_schema(self.output_schema, "output_schema")
        if self.side_effect not in {
            "read_only",
            "write",
            "control_plane_write",
            "project_write",
            "network",
        }:
            raise ValueError("unsupported side_effect")
        if self.network_policy not in {"offline", "network"}:
            raise ValueError("unsupported network_policy")
        if self.side_effect == "network" and self.network_policy != "network":
            raise ValueError("network side_effect requires network_policy='network'")
        if self.network_policy == "network" and self.side_effect != "network":
            raise ValueError("network_policy='network' requires side_effect='network'")
        if self.timeout_ms <= 0:
            raise ValueError("timeout_ms must be greater than zero")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if self.budget_cost <= 0:
            raise ValueError("budget_cost must be greater than zero")
        if self.required_approval_scope is not None and not self.required_approval_scope:
            raise ValueError("required_approval_scope must be non-empty when set")
        object.__setattr__(
            self,
            "allowed_roots",
            tuple(Path(root).expanduser().resolve() for root in self.allowed_roots),
        )
        object.__setattr__(self, "input_schema", _freeze_schema(self.input_schema))
        object.__setattr__(self, "output_schema", _freeze_schema(self.output_schema))


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    tool_version: str
    canonical_args: str
    args_sha256: str
    run_id: str
    step_index: int
    idempotency_key: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.tool_name, "tool_name"),
            (self.tool_version, "tool_version"),
            (self.run_id, "run_id"),
            (self.idempotency_key, "idempotency_key"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be non-empty")
        if not isinstance(self.step_index, int) or isinstance(self.step_index, bool) or self.step_index < 0:
            raise ValueError("step_index must be a non-negative integer")
        try:
            decoded = json.loads(self.canonical_args)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("canonical_args must contain an object") from exc
        if not isinstance(decoded, dict) or canonical_json(decoded) != self.canonical_args:
            raise ValueError("canonical_args must be canonical JSON object")
        reject_sensitive_tool_arguments(decoded)
        if self.args_sha256 != sha256_json(decoded):
            raise ValueError("args_sha256 does not match canonical_args")

    @classmethod
    def create(
        cls,
        *,
        tool_name: str,
        tool_version: str,
        args: Mapping[str, Any],
        run_id: str,
        step_index: int,
        idempotency_key: str,
    ) -> "ToolCall":
        copied_args = copy.deepcopy(dict(args))
        reject_sensitive_tool_arguments(copied_args)
        try:
            canonical_args = canonical_json(copied_args)
        except (TypeError, ValueError) as exc:
            raise ValueError("tool arguments must be JSON serializable") from exc
        return cls(
            tool_name=tool_name,
            tool_version=tool_version,
            canonical_args=canonical_args,
            args_sha256=sha256_json(copied_args),
            run_id=run_id,
            step_index=step_index,
            idempotency_key=idempotency_key,
        )

    @property
    def args(self) -> dict[str, Any]:
        return json.loads(self.canonical_args)

    @property
    def fingerprint(self) -> tuple[str, str, str]:
        """Return the immutable operation identity for idempotency matching."""

        return (self.tool_name, self.tool_version, self.args_sha256)


@dataclass(frozen=True)
class ToolResult:
    status: ToolStatus
    output: Mapping[str, Any] | None
    output_sha256: str | None
    idempotency_key: str
    error_type: str | None
    redacted_summary: str

    def __post_init__(self) -> None:
        if self.status not in {"completed", "blocked", "retry", "quarantined", "error"}:
            raise ValueError("unknown tool result status")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key:
            raise ValueError("idempotency_key must be non-empty")
        if self.output is not None:
            if not isinstance(self.output, Mapping):
                raise ValueError("output must be an object")
            object.__setattr__(self, "output", _deep_freeze(dict(self.output)))
            if self.output_sha256 != sha256_json(self.output):
                raise ValueError("output_sha256 does not match output")
        elif self.output_sha256 is not None:
            raise ValueError("output_sha256 requires output")


@dataclass(frozen=True)
class ToolPreparationResult:
    """Safe canonical arguments and ephemeral references resolved before start."""

    arguments: Mapping[str, Any]
    references: Mapping[str, object] = dataclass_field(default_factory=dict)
    error_type: str | None = None


@dataclass(frozen=True)
class PreparedToolCall:
    call: ToolCall
    error_type: str | None = None


@dataclass(frozen=True)
class _CompletedToolResult:
    fingerprint: tuple[str, str, str]
    result: ToolResult
    terminal: bool


class ToolRegistry:
    """Execute versioned registered handlers through the guardrail boundary."""

    def __init__(self, *, guardrails: GuardrailEngine | None = None) -> None:
        self._guardrails = guardrails or GuardrailEngine()
        self._specs: dict[tuple[str, str], ToolSpec] = {}
        self._handlers: dict[tuple[str, str], ToolHandler] = {}
        self._preparers: dict[tuple[str, str], ToolPreparer] = {}

    def register(
        self,
        spec: ToolSpec,
        handler: ToolHandler,
        *,
        preparer: ToolPreparer | None = None,
    ) -> None:
        if preparer is not None and (
            spec.side_effect != "read_only" or spec.network_policy != "offline"
        ):
            raise ValueError("preparer requires a read-only offline tool")
        if not _capabilities_are_consistent(spec):
            raise ValueError("tool capability classification is inconsistent")
        if _requires_approval(spec) and spec.required_approval_scope is None:
            raise ValueError(
                "write and network capability specs require an approval scope"
            )
        key = (spec.name, spec.version)
        if key in self._specs:
            raise ValueError(f"tool already registered: {spec.name}@{spec.version}")
        self._specs[key] = spec
        self._handlers[key] = handler
        if preparer is not None:
            self._preparers[key] = preparer

    def get(self, name: str, version: str) -> ToolSpec | None:
        return self._specs.get((name, version))

    def prepare(
        self,
        call: ToolCall,
        context: ToolExecutionContext,
    ) -> PreparedToolCall:
        """Resolve read-only inputs before the durable tool request/start boundary."""

        preparer = self._preparers.get((call.tool_name, call.tool_version))
        if preparer is None:
            return PreparedToolCall(call)
        outcome = preparer(call.args, context)
        prepared = ToolCall.create(
            tool_name=call.tool_name,
            tool_version=call.tool_version,
            args=outcome.arguments,
            run_id=call.run_id,
            step_index=call.step_index,
            idempotency_key=call.idempotency_key,
        )
        next_references = dict(context.references)
        next_references.update(copy.deepcopy(dict(outcome.references)))
        context.references = next_references
        return PreparedToolCall(prepared, outcome.error_type)

    def execute(self, call: ToolCall, context: ToolExecutionContext) -> ToolResult:
        spec = self.get(call.tool_name, call.tool_version)
        if spec is None:
            return _failure(call, "blocked", "unknown_tool")
        args = call.args
        if not _matches_schema(args, spec.input_schema):
            return _failure(call, "blocked", "malformed_arguments")
        decision = self._guardrails.check(
            call,
            spec,
            context,
            include_tool_call_budget=False,
        )
        if decision is not GuardrailDecision.ALLOW:
            error_type = self._guardrails.error_type(
                call,
                spec,
                context,
                include_tool_call_budget=False,
            )
            guardrail_statuses: dict[GuardrailDecision, ToolStatus] = {
                GuardrailDecision.REQUIRE_HITL: "retry",
                GuardrailDecision.RETRY: "retry",
                GuardrailDecision.QUARANTINE: "quarantined",
            }
            guardrail_status = guardrail_statuses.get(decision, "blocked")
            return _failure(
                call,
                guardrail_status,
                error_type or "guardrail_blocked",
            )
        ledger_key = (call.run_id, call.step_index, call.idempotency_key)
        with context.idempotency_lock:
            if previous := context.completed_results.get(ledger_key):
                if previous.fingerprint != call.fingerprint:
                    return _failure(call, "blocked", "idempotency_key_collision")
                return previous.result
            if call.fingerprint in context.ambiguous_fingerprints:
                return _failure(
                    call,
                    "blocked",
                    "ambiguous_execution_in_progress",
                )
            decision = self._guardrails.check(call, spec, context)
            if decision is not GuardrailDecision.ALLOW:
                return _failure(
                    call,
                    "blocked",
                    self._guardrails.error_type(call, spec, context)
                    or "guardrail_blocked",
                )
            context.tool_call_count += spec.budget_cost
            reservation = _CompletedToolResult(
                fingerprint=call.fingerprint,
                result=_failure(call, "retry", "tool_in_progress"),
                terminal=False,
            )
            context.completed_results[ledger_key] = reservation
        try:
            isolated_context, reference_snapshot = _snapshot_context(context)
        except Exception:
            return _complete_reservation(
                call,
                context,
                ledger_key,
                reservation,
                _failure(call, "error", "context_snapshot_failed"),
            )
        output, error_type = _invoke_with_timeout(
            self._handlers[(call.tool_name, call.tool_version)],
            args,
            isolated_context,
            spec.timeout_ms,
        )
        if error_type is not None:
            status: ToolStatus = (
                "quarantined"
                if error_type == "execution_timeout_ambiguous"
                else "error"
            )
            if error_type == "execution_timeout_ambiguous":
                with context.idempotency_lock:
                    context.ambiguous_fingerprints.add(call.fingerprint)
            return _complete_reservation(
                call,
                context,
                ledger_key,
                reservation,
                _failure(call, status, error_type),
            )
        assert output is not None
        if not _matches_schema(output, spec.output_schema):
            return _complete_reservation(
                call,
                context,
                ledger_key,
                reservation,
                _failure(call, "error", "output_schema_mismatch"),
            )
        if len(canonical_json(output).encode("utf-8")) > context.max_output_bytes:
            return _complete_reservation(
                call,
                context,
                ledger_key,
                reservation,
                _failure(call, "blocked", "output_bytes_exceeded"),
            )
        result = ToolResult(
            status="completed",
            output=output,
            output_sha256=sha256_json(output),
            idempotency_key=call.idempotency_key,
            error_type=None,
            redacted_summary="Tool completed.",
        )
        try:
            reference_delta = _prepare_reference_delta(
                reference_snapshot,
                isolated_context,
            )
        except Exception:
            return _complete_reservation(
                call,
                context,
                ledger_key,
                reservation,
                _failure(call, "error", "context_merge_failed"),
            )
        return _complete_reservation(
            call,
            context,
            ledger_key,
            reservation,
            result,
            reference_delta=reference_delta,
        )


def _validate_schema(schema: Schema, name: str) -> None:
    if not isinstance(schema, Mapping):
        raise ValueError(f"{name} must be an object schema")
    for field, expected in schema.items():
        if not isinstance(field, str) or not field:
            raise ValueError(f"{name} fields must be non-empty strings")
        if isinstance(expected, tuple):
            if not expected or not all(isinstance(item, type) for item in expected):
                raise ValueError(f"{name} tuple field types must be types")
        elif not isinstance(expected, type):
            raise ValueError(f"{name} field types must be types")


def _matches_schema(value: Mapping[str, Any], schema: Schema) -> bool:
    if set(value) != set(schema):
        return False
    return all(
        isinstance(value[field], expected) and not (expected is int and isinstance(value[field], bool))
        for field, expected in schema.items()
    )


def _freeze_schema(schema: Schema) -> Schema:
    return MappingProxyType(copy.deepcopy(dict(schema)))


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _snapshot_context(
    context: ToolExecutionContext,
) -> tuple[ToolExecutionContext, dict[str, object]]:
    reference_snapshot = copy.deepcopy(context.references)
    return (
        ToolExecutionContext(
            budget=context.budget,
            offline_mode=context.offline_mode,
            tool_call_count=context.tool_call_count,
            retry_count=context.retry_count,
            max_output_bytes=context.max_output_bytes,
            approved_scopes=context.approved_scopes,
            references=copy.deepcopy(reference_snapshot),
            ambiguous_fingerprints=set(context.ambiguous_fingerprints),
        ),
        reference_snapshot,
    )


def _complete_reservation(
    call: ToolCall,
    context: ToolExecutionContext,
    ledger_key: tuple[str, int, str],
    reservation: _CompletedToolResult,
    result: ToolResult,
    *,
    reference_delta: dict[str, object] | None = None,
) -> ToolResult:
    with context.idempotency_lock:
        current = context.completed_results.get(ledger_key)
        if current is not reservation:
            return current.result if current is not None else result
        try:
            next_references = dict(context.references)
            if reference_delta is not None:
                next_references.update(reference_delta)
        except Exception:
            result = _failure(call, "error", "context_merge_failed")
        else:
            context.references = next_references
        context.completed_results[ledger_key] = _CompletedToolResult(
            fingerprint=reservation.fingerprint,
            result=result,
            terminal=True,
        )
    return result


def _prepare_reference_delta(
    reference_snapshot: dict[str, object],
    isolated_context: ToolExecutionContext,
) -> dict[str, object]:
    delta: dict[str, object] = {}
    for key, value in isolated_context.references.items():
        if not isinstance(key, str) or not key:
            raise ValueError("context reference keys must be non-empty strings")
        if key not in reference_snapshot or reference_snapshot[key] != value:
            delta[key] = copy.deepcopy(value)
    return delta


def _invoke_with_timeout(
    handler: ToolHandler,
    args: dict[str, Any],
    context: ToolExecutionContext,
    timeout_ms: int,
) -> tuple[dict[str, Any] | None, str | None]:
    outcome: dict[str, object] = {}

    def invoke() -> None:
        try:
            outcome["output"] = dict(handler(args, context))
        except Exception as exc:  # pragma: no cover - asserted through error_type
            outcome["error_type"] = type(exc).__name__

    thread = threading.Thread(target=invoke, daemon=True)
    thread.start()
    thread.join(timeout_ms / 1000)
    if thread.is_alive():
        return None, "execution_timeout_ambiguous"
    error_type = outcome.get("error_type")
    if isinstance(error_type, str):
        return None, error_type
    output = outcome.get("output")
    if not isinstance(output, dict):
        return None, "invalid_handler_output"
    return output, None


def _failure(call: ToolCall, status: ToolStatus, error_type: str) -> ToolResult:
    return ToolResult(
        status=status,
        output=None,
        output_sha256=None,
        idempotency_key=call.idempotency_key,
        error_type=error_type,
        redacted_summary=str(_redact({"error_type": error_type})["error_type"]),
    )


def reject_sensitive_tool_arguments(value: object) -> None:
    """Fail before canonicalization when parsed arguments contain credentials."""

    if _contains_sensitive_tool_argument(value):
        raise SensitiveToolArgumentError(
            "sensitive tool arguments are not permitted in the durable protocol"
        )


def _contains_sensitive_tool_argument(value: object) -> bool:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().replace("-", "_")
            key = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
            if (
                key in _SENSITIVE_ARGUMENT_KEYS
                or _PROVIDER_CREDENTIAL_KEY.fullmatch(key) is not None
            ):
                return True
            if _contains_sensitive_tool_argument(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_tool_argument(item) for item in value)
    if isinstance(value, str):
        return any(pattern.search(value) is not None for pattern in _SENSITIVE_SCALAR_PATTERNS)
    return False


def _requires_approval(spec: ToolSpec) -> bool:
    return (
        spec.side_effect != "read_only"
        or spec.network_policy == "network"
        or spec.required_approval_scope is not None
    )


def _capabilities_are_consistent(spec: ToolSpec) -> bool:
    return (spec.side_effect == "network") == (spec.network_policy == "network")
