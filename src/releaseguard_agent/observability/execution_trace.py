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
from typing import Any, Iterator


EXECUTION_TRACE_SCHEMA_VERSION = "1.0"
_SENSITIVE_KEY = re.compile(
    r"api[_-]?key|token|password|secret|authorization|credential",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(r"(?:sk|key|token)-[A-Za-z0-9_-]{8,}")


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
            event = {
                "event_id": f"evt-{uuid.uuid4()}",
                "kind": kind,
                "node": node,
                "tool": tool,
                "start": started_at,
                "end": _utc_now(),
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "status": status,
                "error_type": error_type,
                **span.details,
            }
            redacted = _redact(event)
            with self._lock:
                self._events.append(redacted)
            if self._event_callback is not None:
                self._event_callback(dict(redacted))

    def route(self, source: str, destination: str) -> None:
        now = _utc_now()
        event = {
                    "event_id": f"evt-{uuid.uuid4()}",
                    "kind": "route",
                    "node": source,
                    "tool": None,
                    "start": now,
                    "end": now,
                    "latency_ms": 0.0,
                    "status": "success",
                    "route": destination,
                    "error_type": None,
                }
        with self._lock:
            self._events.append(event)
        if self._event_callback is not None:
            self._event_callback(dict(event))

    def to_dict(
        self,
        *,
        artifact_paths: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            events = [dict(event) for event in self._events]
        return {
            "tool": "releaseguard-agent",
            "artifact_type": "execution_trace",
            "schema_version": EXECUTION_TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "start": self.started_at,
            "end": _utc_now(),
            "status": "error" if any(
                event["status"] == "error" for event in events
            ) else "success",
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
        return {str(item_key): _redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[REDACTED]", value)
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
