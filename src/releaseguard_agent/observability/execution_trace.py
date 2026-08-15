import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any, Iterator, Mapping


EXECUTION_TRACE_SCHEMA_VERSION = "1.3"
_RUNTIME_EVENT_PHASE = 100
_GUARDRAIL_PHASE = 200
_SENSITIVE_KEY = re.compile(
    r"api[_-]?key|token|password|secret|authorization|credential",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:"
    r"(?:sk|key|token)-[A-Za-z0-9_-]{8,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
    r"|[a-z][a-z0-9+.-]*://[^\s:/]+:[^@\s]+@[^\s]+"
    r"|-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*?"
    r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----"
    r")",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class TraceSpan:
    kind: str
    node: str | None = None
    tool: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def update(self, **details: Any) -> None:
        self.details.update(details)


@dataclass(frozen=True)
class ExecutionTraceArtifacts:
    output_dir: Path
    trace_path: Path


@dataclass(frozen=True)
class ArtifactContextTrace:
    """ID-only replay metadata for immutable relation and memory support."""

    relation_index_version: str | None = None
    relation_sha256: str | None = None
    relation_mode: str = "text_only"
    relation_fallback_reason: str | None = None
    relation_candidate_ids: tuple[str, ...] = ()
    relation_path_ids: tuple[str, ...] = ()
    relation_budget: tuple[tuple[str, int], ...] = ()
    relation_budget_usage: tuple[tuple[str, int], ...] = ()
    memory_version: str | None = None
    memory_sha256: str | None = None
    memory_mode: str = "not_requested"
    memory_fallback_reason: str | None = None
    selected_memory_ids: tuple[str, ...] = ()
    omitted_memory: tuple[tuple[str, str], ...] = ()
    memory_budget: tuple[tuple[str, int], ...] = ()
    memory_budget_usage: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        for value, prefix, label in (
            (self.relation_index_version, "ri-", "relation_index_version"),
            (self.memory_version, "pm-", "memory_version"),
        ):
            if value is not None and (
                not isinstance(value, str)
                or len(value) != 67
                or not value.startswith(prefix)
                or not _is_lower_sha256(value[3:])
            ):
                raise ValueError(f"{label} is invalid")
        for value, label in (
            (self.relation_sha256, "relation_sha256"),
            (self.memory_sha256, "memory_sha256"),
        ):
            if value is not None and not _is_lower_sha256(value):
                raise ValueError(f"{label} is invalid")
        for value, label in (
            (self.relation_mode, "relation_mode"),
            (self.memory_mode, "memory_mode"),
        ):
            _safe_trace_value(value, label)
        for value, label in (
            (self.relation_fallback_reason, "relation_fallback_reason"),
            (self.memory_fallback_reason, "memory_fallback_reason"),
        ):
            if value is not None:
                _safe_trace_value(value, label)
        for id_values, label in (
            (self.relation_candidate_ids, "relation_candidate_ids"),
            (self.relation_path_ids, "relation_path_ids"),
            (self.selected_memory_ids, "selected_memory_ids"),
        ):
            if tuple(sorted(set(id_values))) != id_values:
                raise ValueError(f"{label} must be sorted and unique")
            for value in id_values:
                _safe_trace_value(value, label)
        for budget_values, label, allowed_keys in (
            (
                self.relation_budget,
                "relation_budget",
                {"max_context_characters", "max_edges", "max_hops", "max_nodes"},
            ),
            (
                self.relation_budget_usage,
                "relation_budget_usage",
                {"context_characters", "edges", "nodes"},
            ),
            (
                self.memory_budget,
                "memory_budget",
                {"max_characters", "max_estimated_units", "top_k"},
            ),
            (
                self.memory_budget_usage,
                "memory_budget_usage",
                {"characters", "estimated_units"},
            ),
        ):
            if (
                tuple(sorted(budget_values)) != budget_values
                or len(dict(budget_values)) != len(budget_values)
            ):
                raise ValueError(f"{label} must be sorted and unique")
            if not {key for key, _ in budget_values} <= allowed_keys:
                raise ValueError(f"{label} budget keys are invalid")
            if any(
                not isinstance(item, int) or isinstance(item, bool) or item < 0
                for item in dict(budget_values).values()
            ):
                raise ValueError(f"{label} values must be non-negative integers")
        if tuple(sorted(self.omitted_memory)) != self.omitted_memory:
            raise ValueError("omitted_memory must be sorted")
        for memory_id, reason in self.omitted_memory:
            _safe_trace_value(memory_id, "omitted_memory memory_id")
            _safe_trace_value(reason, "omitted_memory reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_index_version": self.relation_index_version,
            "relation_sha256": self.relation_sha256,
            "relation_mode": self.relation_mode,
            "relation_fallback_reason": self.relation_fallback_reason,
            "relation_candidate_ids": list(self.relation_candidate_ids),
            "relation_path_ids": list(self.relation_path_ids),
            "relation_budget": dict(self.relation_budget),
            "relation_budget_usage": dict(self.relation_budget_usage),
            "memory_version": self.memory_version,
            "memory_sha256": self.memory_sha256,
            "memory_mode": self.memory_mode,
            "memory_fallback_reason": self.memory_fallback_reason,
            "selected_memory_ids": list(self.selected_memory_ids),
            "omitted_memory": [
                {"memory_id": memory_id, "reason": reason}
                for memory_id, reason in self.omitted_memory
            ],
            "memory_budget": dict(self.memory_budget),
            "memory_budget_usage": dict(self.memory_budget_usage),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactContextTrace":
        expected = {
            "relation_index_version", "relation_sha256", "relation_mode",
            "relation_fallback_reason", "relation_candidate_ids",
            "relation_path_ids", "relation_budget", "relation_budget_usage",
            "memory_version", "memory_sha256", "memory_mode",
            "memory_fallback_reason", "selected_memory_ids", "omitted_memory",
            "memory_budget", "memory_budget_usage",
        }
        if set(value) != expected:
            raise ValueError("artifact_context keys do not match the trace schema")
        omitted = value["omitted_memory"]
        if not isinstance(omitted, (list, tuple)):
            raise ValueError("omitted_memory must be a list")
        omitted_pairs: list[tuple[str, str]] = []
        for item in omitted:
            if not isinstance(item, Mapping) or set(item) != {"memory_id", "reason"}:
                raise ValueError("omitted_memory entries are invalid")
            omitted_pairs.append(
                (
                    _required_string(item["memory_id"], "omitted memory_id"),
                    _required_string(item["reason"], "omitted reason"),
                )
            )
        return cls(
            relation_index_version=_optional_string(value["relation_index_version"]),
            relation_sha256=_optional_string(value["relation_sha256"]),
            relation_mode=_required_string(value["relation_mode"], "relation_mode"),
            relation_fallback_reason=_optional_string(value["relation_fallback_reason"]),
            relation_candidate_ids=_string_tuple(value["relation_candidate_ids"]),
            relation_path_ids=_string_tuple(value["relation_path_ids"]),
            relation_budget=_integer_pairs(value["relation_budget"]),
            relation_budget_usage=_integer_pairs(value["relation_budget_usage"]),
            memory_version=_optional_string(value["memory_version"]),
            memory_sha256=_optional_string(value["memory_sha256"]),
            memory_mode=_required_string(value["memory_mode"], "memory_mode"),
            memory_fallback_reason=_optional_string(value["memory_fallback_reason"]),
            selected_memory_ids=_string_tuple(value["selected_memory_ids"]),
            omitted_memory=tuple(omitted_pairs),
            memory_budget=_integer_pairs(value["memory_budget"]),
            memory_budget_usage=_integer_pairs(value["memory_budget_usage"]),
        )


class ExecutionTracer:
    """Thread-safe, redacting event recorder for one workflow run."""

    def __init__(
        self,
        run_id: str | None = None,
        *,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.run_id = run_id or f"rg-{uuid.uuid4()}"
        self.started_at = _utc_now()
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._event_callback = event_callback

    @contextmanager
    def span(
        self,
        kind: str,
        *,
        node: str | None = None,
        tool: str | None = None,
        **details: Any,
    ) -> Iterator[TraceSpan]:
        started_at = _utc_now()
        started = time.perf_counter()
        span = TraceSpan(kind=kind, node=node, tool=tool, details=dict(details))
        status = "success"
        error_type: str | None = None
        try:
            yield span
        except Exception as exc:
            status = "error"
            error_type = type(exc).__name__
            raise
        finally:
            event_run_id = str(span.details.pop("run_id", self.run_id))
            event: dict[str, Any] = {
                "event_id": f"evt-{uuid.uuid4()}",
                "run_id": event_run_id,
                "kind": kind,
                "node": node,
                "tool": tool,
                "start": started_at,
                "end": _utc_now(),
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "cost": {},
                "status": status,
                "error_type": error_type,
                **span.details,
            }
            self._record(event)

    def route(self, source: str, destination: str) -> None:
        now = _utc_now()
        event: dict[str, Any] = {
            "event_id": f"evt-{uuid.uuid4()}",
            "run_id": self.run_id,
            "kind": "route",
            "node": source,
            "tool": None,
            "start": now,
            "end": now,
            "latency_ms": 0.0,
            "cost": {},
            "status": "success",
            "route": destination,
            "error_type": None,
        }
        self._record(event)

    def runtime_event(
        self,
        *,
        run_id: str,
        event_kind: str,
        event_sequence: int,
        step_index: int | None = None,
        idempotency_key: str | None = None,
        guardrail_decision: str | None = None,
        approval_id: str | None = None,
        checkpoint_sequence: int | None = None,
        cost: Mapping[str, Any] | None = None,
        artifact_context: ArtifactContextTrace | None = None,
        occurred_at: str | None = None,
    ) -> None:
        """Record one durable runtime transition without copying raw payloads."""

        now = occurred_at or _utc_now()
        ordering_key = _ordering_key(event_sequence, _RUNTIME_EVENT_PHASE)
        status, error_type = _runtime_event_status(event_kind)
        self._record(
            {
                "event_id": f"runtime:{run_id}:{ordering_key}",
                "observation_identity": f"runtime:{run_id}:{event_sequence}",
                "ordering_key": ordering_key,
                "run_id": run_id,
                "kind": "runtime_event",
                "node": None,
                "tool": None,
                "start": now,
                "end": now,
                "latency_ms": 0.0,
                "cost": dict(cost or {}),
                "status": status,
                "error_type": error_type,
                "event_kind": event_kind,
                "event_sequence": event_sequence,
                "step_index": step_index,
                "idempotency_key": idempotency_key,
                "guardrail_decision": guardrail_decision,
                "approval_id": approval_id,
                "checkpoint_sequence": checkpoint_sequence,
                "artifact_context": (
                    artifact_context.to_dict()
                    if artifact_context is not None
                    else None
                ),
            }
        )

    def guardrail(
        self,
        *,
        run_id: str,
        step_index: int,
        idempotency_key: str,
        decision: str,
        approval_id: str | None = None,
        event_sequence: int | None = None,
        ordering_event_sequence: int | None = None,
        occurred_at: str | None = None,
    ) -> None:
        """Record the explicit policy decision made before a tool invocation."""

        now = occurred_at or _utc_now()
        identity_sequence = (
            event_sequence if event_sequence is not None else step_index
        )
        ordering_sequence = (
            ordering_event_sequence
            if ordering_event_sequence is not None
            else identity_sequence
        )
        ordering_key = _ordering_key(ordering_sequence, _GUARDRAIL_PHASE)
        self._record(
            {
                "event_id": (
                    f"guardrail:{run_id}:{ordering_key}:{decision}"
                ),
                "observation_identity": (
                    f"guardrail:{run_id}:{identity_sequence}:{decision}"
                ),
                "ordering_key": ordering_key,
                "run_id": run_id,
                "kind": "guardrail",
                "node": None,
                "tool": None,
                "start": now,
                "end": now,
                "latency_ms": 0.0,
                "cost": {},
                "status": "success",
                "error_type": None,
                "step_index": step_index,
                "idempotency_key": idempotency_key,
                "guardrail_decision": decision,
                "approval_id": approval_id,
                "checkpoint_sequence": None,
            }
        )

    def _record(self, event: dict[str, Any]) -> None:
        redacted = _redact(event)
        identity = redacted.get("observation_identity", redacted.get("event_id"))
        with self._lock:
            existing = next(
                (
                    item
                    for item in self._events
                    if item.get(
                        "observation_identity",
                        item.get("event_id"),
                    )
                    == identity
                ),
                None,
            )
            if existing is not None:
                if existing != redacted:
                    raise ValueError(
                        "trace observation identity has conflicting data"
                    )
                return
            self._events.append(redacted)
        if self._event_callback is not None:
            self._event_callback(dict(redacted))

    def to_dict(
        self,
        *,
        artifact_paths: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            events = [dict(event) for event in self._events]
        overall_status = _overall_trace_status(events)
        return {
            "tool": "releaseguard-agent",
            "artifact_type": "execution_trace",
            "schema_version": EXECUTION_TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "start": self.started_at,
            "end": _utc_now(),
            "status": overall_status,
            "artifact_paths": _redact(artifact_paths or {}),
            "events": events,
        }

    def write(self, output_dir: Path) -> ExecutionTraceArtifacts:
        normalized = Path(output_dir).expanduser().resolve()
        normalized.mkdir(parents=True, exist_ok=True)
        trace_path = normalized / "execution_trace.json"
        payload = self.to_dict(
            artifact_paths={"execution_trace": str(trace_path)}
        )
        trace_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        return ExecutionTraceArtifacts(normalized, trace_path)


def _redact(value: Any, key: str | None = None) -> Any:
    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        original_keys = tuple(str(item_key) for item_key in value)
        key_replacements = _redacted_key_replacements(original_keys)
        for item_key, item in value.items():
            original_key = str(item_key)
            durable_key = key_replacements.get(original_key, original_key)
            redacted[durable_key] = _redact(item, original_key)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[REDACTED]", value)
    return value


def _runtime_event_status(event_kind: str) -> tuple[str, str | None]:
    if event_kind in {"TOOL_FAILED", "RUN_FAILED"}:
        return "error", event_kind.lower()
    if event_kind == "RUN_PAUSED":
        return "paused", None
    if event_kind == "RUN_CANCELLED":
        return "cancelled", None
    return "success", None


def _overall_trace_status(events: list[dict[str, Any]]) -> str:
    """Prefer the final durable terminal state over earlier retry history."""

    terminal_statuses = {
        "RUN_COMPLETED": "success",
        "RUN_FAILED": "error",
        "RUN_PAUSED": "paused",
        "RUN_CANCELLED": "cancelled",
    }
    runtime_events = sorted(
        (
            event
            for event in events
            if event.get("kind") == "runtime_event"
            and isinstance(event.get("event_sequence"), int)
        ),
        key=lambda event: int(event["event_sequence"]),
    )
    for event in reversed(runtime_events):
        event_kind = event.get("event_kind")
        durable_status = (
            terminal_statuses.get(event_kind)
            if isinstance(event_kind, str)
            else None
        )
        if durable_status is not None:
            return durable_status
    if any(event["status"] == "error" for event in events):
        return "error"
    if any(event["status"] == "paused" for event in events):
        return "paused"
    if any(event["status"] == "cancelled" for event in events):
        return "cancelled"
    return "success"


def _key_requires_redaction(key: str) -> bool:
    return bool(_SENSITIVE_KEY.search(key) or _SECRET_VALUE.search(key))


def _redacted_key_replacements(keys: tuple[str, ...]) -> dict[str, str]:
    reserved = set(keys)
    replacements: dict[str, str] = {}
    next_index = 1
    for key in sorted(item for item in keys if _key_requires_redaction(item)):
        while True:
            candidate = f"[REDACTED_KEY_{next_index}]"
            next_index += 1
            if candidate not in reserved:
                break
        replacements[key] = candidate
        reserved.add(candidate)
    return replacements


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ordering_key(event_sequence: int, phase: int) -> str:
    if isinstance(event_sequence, bool) or event_sequence < 0:
        raise ValueError("trace event sequence must be non-negative")
    return f"{event_sequence:020d}:{phase:03d}"


def _is_lower_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_trace_value(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or _SECRET_VALUE.search(value) is not None
    ):
        raise ValueError(f"{label} must be a safe non-empty identifier")


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("artifact_context optional values must be strings or null")
    return value


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"artifact_context {label} values must be strings")
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError("artifact_context identifiers must be a list of strings")
    return tuple(value)


def _integer_pairs(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, Mapping):
        raise ValueError("artifact_context budgets must be objects")
    if any(not isinstance(key, str) for key in value):
        raise ValueError("artifact_context budget keys must be strings")
    pairs = tuple(sorted((key, item) for key, item in value.items()))
    if any(not isinstance(item, int) or isinstance(item, bool) for _, item in pairs):
        raise ValueError("artifact_context budget values must be integers")
    return pairs
