"""SQLite-backed, append-only persistence for durable Agent runs."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, cast

from releaseguard_agent.observability.execution_trace import _redact

from .models import AgentRunState, RunEvent, canonical_json, sha256_json
from .reducer import (
    InvalidTransitionError,
    reduce_event,
    validate_tool_requested_payload_pre_envelope,
)
from .tools import ToolCall, ToolResult, ToolStatus


class StaleSequenceError(RuntimeError):
    """Raised when an append does not match the current run sequence."""


class CorruptRunError(RuntimeError):
    """Raised when persisted events or the materialized snapshot are invalid."""


@dataclass(frozen=True)
class ToolExecutionAttempt:
    """Durable ownership and ambiguity record for one handler invocation."""

    run_id: str
    step_index: int
    idempotency_key: str
    tool_name: str
    tool_version: str
    args_sha256: str
    attempt_id: str
    owner_id: str
    status: str
    started_at_utc: str
    updated_at_utc: str
    reason: str | None = None
    result_digest: str | None = None

    @property
    def fingerprint(self) -> tuple[str, str, str]:
        """Return the immutable operation identity reserved by this attempt."""

        return (self.tool_name, self.tool_version, self.args_sha256)


class AgentRunStore:
    """Own one SQLite connection and atomically persist event/state pairs."""

    def __init__(
        self,
        root: Path,
        *,
        allowed_runtime_root: Path | None = None,
    ) -> None:
        configured_root = allowed_runtime_root
        if configured_root is None and os.name != "nt":
            configured = os.environ.get("RELEASEGUARD_RUNTIME_ROOT")
            configured_root = (
                Path(configured)
                if configured
                else Path(__file__).resolve().parents[3] / ".runtime"
            )
        self._root = validate_runtime_root(
            Path(root),
            allowed_runtime_root=configured_root,
        )
        self._root.mkdir(parents=True, exist_ok=True)
        self._database_path = self._root / "agent_runs.sqlite3"
        self._connection = sqlite3.connect(self._database_path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS run_events (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                event_kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                PRIMARY KEY(run_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS run_snapshots (
                run_id TEXT PRIMARY KEY,
                sequence INTEGER NOT NULL,
                state_json TEXT NOT NULL,
                state_sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tool_results (
                run_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                tool_version TEXT NOT NULL,
                args_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                output_json TEXT,
                output_sha256 TEXT,
                error_type TEXT,
                redacted_summary TEXT NOT NULL,
                PRIMARY KEY(run_id, step_index, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS tool_execution_attempts (
                run_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                tool_version TEXT NOT NULL,
                args_sha256 TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                reason TEXT,
                result_digest TEXT,
                PRIMARY KEY(run_id, step_index, idempotency_key)
            );
            """
        )
        self._connection.commit()

    def close(self) -> None:
        """Release this store's dedicated SQLite connection."""

        self._connection.close()

    @property
    def root(self) -> Path:
        """Return the platform-policy-approved directory owned by this store."""

        return self._root

    def create_run(
        self,
        state: AgentRunState,
        *,
        request: Mapping[str, object] | None = None,
    ) -> RunEvent:
        """Create a run by atomically writing its required sequence-zero event."""

        if state.status != "CREATED" or state.last_event_sequence != -1:
            raise ValueError("create_run requires an initial CREATED state")
        payload: dict[str, object] = {"state": state.to_dict()}
        if request is not None:
            payload["request"] = dict(request)
        event = RunEvent.create(
            event_id=uuid.uuid4().hex,
            run_id=state.run_id,
            sequence=0,
            event_kind="RUN_CREATED",
            payload=_redacted_mapping(payload),
            created_at_utc=_utc_now(),
        )
        next_state = self._reduce(None, event)
        self._begin()
        try:
            existing = self._connection.execute(
                "SELECT 1 FROM run_events WHERE run_id = ? LIMIT 1", (state.run_id,)
            ).fetchone()
            if existing is not None:
                raise ValueError(f"run already exists: {state.run_id}")
            self._insert_event(event)
            self._write_snapshot(next_state)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return event

    def append(
        self, run_id: str, expected_sequence: int, event_kind: str, payload: Mapping[str, object]
    ) -> RunEvent:
        """Compare-and-swap one next event and the resulting snapshot in one transaction."""

        if event_kind == "TOOL_REQUESTED":
            validate_tool_requested_payload_pre_envelope(payload)
        redacted_payload = _redacted_mapping(payload)
        self._begin()
        try:
            state = self.load_state(run_id)
            if state.last_event_sequence != expected_sequence:
                raise StaleSequenceError(
                    f"expected sequence {expected_sequence}, current sequence is {state.last_event_sequence}"
                )
            event = RunEvent.create(
                event_id=uuid.uuid4().hex,
                run_id=run_id,
                sequence=expected_sequence + 1,
                event_kind=event_kind,
                payload=redacted_payload,
                created_at_utc=_utc_now(),
            )
            next_state = self._reduce(state, event)
            self._insert_event(event)
            self._write_snapshot(next_state)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return event

    def load_state(self, run_id: str) -> AgentRunState:
        """Replay and validate the event stream, including its stored snapshot."""

        state = self._replay(run_id)
        snapshot = self._connection.execute(
            "SELECT sequence, state_json, state_sha256 FROM run_snapshots WHERE run_id = ?", (run_id,)
        ).fetchone()
        if snapshot is None:
            raise CorruptRunError("missing snapshot")
        sequence, state_json, state_sha256 = snapshot
        try:
            raw_state = json.loads(state_json)
            stored_state = AgentRunState.from_dict(raw_state)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CorruptRunError("invalid snapshot") from exc
        if state_sha256 != sha256_json(raw_state):
            raise CorruptRunError("snapshot hash does not match snapshot payload")
        if sequence != state.last_event_sequence or stored_state != state:
            raise CorruptRunError("snapshot does not match replayed event stream")
        return state

    def events(self, run_id: str) -> tuple[RunEvent, ...]:
        """Return individually validated events in canonical sequence order."""

        rows = self._connection.execute(
            """SELECT event_id, run_id, sequence, event_kind, payload_json, payload_sha256, created_at_utc
               FROM run_events WHERE run_id = ? ORDER BY sequence ASC, event_id ASC""",
            (run_id,),
        ).fetchall()
        return tuple(self._event_from_row(row) for row in rows)

    def record_tool_result(
        self,
        call: ToolCall,
        result: ToolResult,
    ) -> ToolResult:
        """Durably retain one exact tool outcome before its lifecycle event."""

        self._begin()
        try:
            durable_result = self._record_tool_result_locked(call, result)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return durable_result

    def record_completed_tool_result(
        self,
        call: ToolCall,
        result: ToolResult,
    ) -> ToolResult:
        """Atomically store one outcome and settle its reserved attempt."""

        self._begin()
        try:
            durable_result = self._record_tool_result_locked(call, result)
            attempt = self._load_tool_attempt_locked(call)
            if attempt is None:
                raise CorruptRunError("tool result has no durable execution attempt")
            if attempt.fingerprint != call.fingerprint:
                raise CorruptRunError("tool execution attempt fingerprint does not match")
            result_digest = _durable_tool_result_digest(durable_result)
            if attempt.status == "COMPLETED":
                if attempt.result_digest != result_digest:
                    raise CorruptRunError(
                        "completed tool attempt result digest does not match"
                    )
            elif attempt.status == "STARTED":
                self._connection.execute(
                    """UPDATE tool_execution_attempts
                       SET status = 'COMPLETED', updated_at_utc = ?,
                           reason = NULL, result_digest = ?
                       WHERE run_id = ? AND step_index = ? AND idempotency_key = ?""",
                    (
                        _utc_now(),
                        result_digest,
                        call.run_id,
                        call.step_index,
                        call.idempotency_key,
                    ),
                )
            else:
                raise CorruptRunError(
                    "ambiguous tool execution attempt cannot accept a result"
                )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return durable_result

    def reconcile_completed_tool_attempt(self, call: ToolCall) -> ToolResult:
        """Settle a historical result-before-attempt crash after exact replay."""

        self._begin()
        try:
            row = self._load_tool_result_row(call)
            if row is None:
                raise CorruptRunError("tool attempt reconciliation has no result")
            result = self._tool_result_from_row(row, call)
            attempt = self._load_tool_attempt_locked(call)
            if attempt is None or attempt.fingerprint != call.fingerprint:
                raise CorruptRunError("tool attempt reconciliation is invalid")
            result_digest = _durable_tool_result_digest(result)
            if attempt.status == "COMPLETED":
                if attempt.result_digest != result_digest:
                    raise CorruptRunError(
                        "completed tool attempt result digest does not match"
                    )
            elif attempt.status == "STARTED":
                self._connection.execute(
                    """UPDATE tool_execution_attempts
                       SET status = 'COMPLETED', updated_at_utc = ?,
                           reason = NULL, result_digest = ?
                       WHERE run_id = ? AND step_index = ? AND idempotency_key = ?""",
                    (
                        _utc_now(),
                        result_digest,
                        call.run_id,
                        call.step_index,
                        call.idempotency_key,
                    ),
                )
            else:
                raise CorruptRunError(
                    "ambiguous tool execution attempt cannot be reconciled"
                )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return result

    def load_tool_result(self, call: ToolCall) -> ToolResult | None:
        """Load an exact durable outcome or fail closed on fingerprint drift."""

        row = self._load_tool_result_row(call)
        return self._tool_result_from_row(row, call) if row is not None else None

    def start_tool_attempt(
        self,
        call: ToolCall,
        *,
        owner_id: str,
    ) -> ToolExecutionAttempt:
        """Reserve one durable handler attempt, returning any active owner."""

        if not owner_id:
            raise ValueError("owner_id must be non-empty")
        self._begin()
        try:
            row = self._connection.execute(
                """SELECT run_id, step_index, idempotency_key, tool_name,
                          tool_version, args_sha256, attempt_id, owner_id,
                          status, started_at_utc, updated_at_utc, reason,
                          result_digest
                   FROM tool_execution_attempts
                   WHERE run_id = ? AND (
                       (step_index = ? AND idempotency_key = ?)
                       OR (
                           tool_name = ? AND tool_version = ? AND args_sha256 = ?
                           AND status IN ('STARTED', 'AMBIGUOUS')
                       )
                   )
                   ORDER BY CASE WHEN step_index = ? AND idempotency_key = ?
                                 THEN 0 ELSE 1 END, started_at_utc ASC
                   LIMIT 1""",
                (
                    call.run_id,
                    call.step_index,
                    call.idempotency_key,
                    call.tool_name,
                    call.tool_version,
                    call.args_sha256,
                    call.step_index,
                    call.idempotency_key,
                ),
            ).fetchone()
            if row is not None:
                attempt = _execution_attempt_from_row(row)
                self._connection.commit()
                return attempt
            now = _utc_now()
            attempt = ToolExecutionAttempt(
                run_id=call.run_id,
                step_index=call.step_index,
                idempotency_key=call.idempotency_key,
                tool_name=call.tool_name,
                tool_version=call.tool_version,
                args_sha256=call.args_sha256,
                attempt_id=uuid.uuid4().hex,
                owner_id=owner_id,
                status="STARTED",
                started_at_utc=now,
                updated_at_utc=now,
            )
            self._connection.execute(
                """INSERT INTO tool_execution_attempts VALUES (
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                   )""",
                (
                    attempt.run_id,
                    attempt.step_index,
                    attempt.idempotency_key,
                    attempt.tool_name,
                    attempt.tool_version,
                    attempt.args_sha256,
                    attempt.attempt_id,
                    attempt.owner_id,
                    attempt.status,
                    attempt.started_at_utc,
                    attempt.updated_at_utc,
                    attempt.reason,
                    attempt.result_digest,
                ),
            )
            self._connection.commit()
            return attempt
        except BaseException:
            self._connection.rollback()
            raise

    def load_tool_attempt(self, call: ToolCall) -> ToolExecutionAttempt | None:
        return self._load_tool_attempt_locked(call)

    def _load_tool_attempt_locked(
        self,
        call: ToolCall,
    ) -> ToolExecutionAttempt | None:
        row = self._connection.execute(
            """SELECT run_id, step_index, idempotency_key, tool_name,
                      tool_version, args_sha256, attempt_id, owner_id,
                      status, started_at_utc, updated_at_utc, reason,
                      result_digest
               FROM tool_execution_attempts
               WHERE run_id = ? AND step_index = ? AND idempotency_key = ?""",
            (call.run_id, call.step_index, call.idempotency_key),
        ).fetchone()
        return _execution_attempt_from_row(row) if row is not None else None

    def _record_tool_result_locked(
        self,
        call: ToolCall,
        result: ToolResult,
    ) -> ToolResult:
        durable_result = _redacted_tool_result(result)
        existing = self._load_tool_result_row(call)
        if existing is not None:
            stored = self._tool_result_from_row(existing, call)
            if stored != durable_result:
                raise CorruptRunError(
                    "durable idempotency result does not match repeated result"
                )
            return stored
        self._connection.execute(
            """INSERT INTO tool_results(
                   run_id, step_index, idempotency_key, tool_name,
                   tool_version, args_sha256, status, output_json,
                   output_sha256, error_type, redacted_summary
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                call.run_id,
                call.step_index,
                call.idempotency_key,
                call.tool_name,
                call.tool_version,
                call.args_sha256,
                durable_result.status,
                (
                    canonical_json(durable_result.output)
                    if durable_result.output is not None
                    else None
                ),
                durable_result.output_sha256,
                durable_result.error_type,
                durable_result.redacted_summary,
            ),
        )
        return durable_result

    def mark_tool_attempt_ambiguous(self, call: ToolCall, *, reason: str) -> None:
        self._update_tool_attempt(
            call,
            status="AMBIGUOUS",
            reason=reason,
            result_digest=None,
        )

    def complete_tool_attempt(self, call: ToolCall, *, result_digest: str) -> None:
        self._update_tool_attempt(
            call,
            status="COMPLETED",
            reason=None,
            result_digest=result_digest,
        )

    def _update_tool_attempt(
        self,
        call: ToolCall,
        *,
        status: str,
        reason: str | None,
        result_digest: str | None,
    ) -> None:
        updated = self._connection.execute(
            """UPDATE tool_execution_attempts
               SET status = ?, updated_at_utc = ?, reason = ?, result_digest = ?
               WHERE run_id = ? AND step_index = ? AND idempotency_key = ?""",
            (
                status,
                _utc_now(),
                reason,
                result_digest,
                call.run_id,
                call.step_index,
                call.idempotency_key,
            ),
        )
        if updated.rowcount != 1:
            self._connection.rollback()
            raise CorruptRunError("tool execution attempt does not exist")
        self._connection.commit()

    def _begin(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _load_tool_result_row(
        self,
        call: ToolCall,
    ) -> tuple[object, ...] | None:
        return self._connection.execute(
            """SELECT tool_name, tool_version, args_sha256, status,
                      output_json, output_sha256, error_type, redacted_summary
               FROM tool_results
               WHERE run_id = ? AND step_index = ? AND idempotency_key = ?""",
            (call.run_id, call.step_index, call.idempotency_key),
        ).fetchone()

    @staticmethod
    def _tool_result_from_row(
        row: tuple[object, ...],
        call: ToolCall,
    ) -> ToolResult:
        (
            tool_name,
            tool_version,
            args_sha256,
            status,
            output_json,
            output_sha256,
            error_type,
            redacted_summary,
        ) = row
        if (tool_name, tool_version, args_sha256) != call.fingerprint:
            raise CorruptRunError("durable idempotency fingerprint does not match call")
        try:
            output: Mapping[str, Any] | None = (
                json.loads(output_json) if isinstance(output_json, str) else None
            )
            if output is not None and not isinstance(output, dict):
                raise ValueError("stored tool output is not an object")
            if not all(
                value is None or isinstance(value, str)
                for value in (output_sha256, error_type)
            ):
                raise ValueError("stored tool result has invalid optional fields")
            if not isinstance(status, str) or not isinstance(redacted_summary, str):
                raise ValueError("stored tool result has invalid fields")
            return ToolResult(
                status=cast(ToolStatus, status),
                output=output,
                output_sha256=cast(str | None, output_sha256),
                idempotency_key=call.idempotency_key,
                error_type=cast(str | None, error_type),
                redacted_summary=redacted_summary,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CorruptRunError("durable tool result is invalid") from exc

    def _replay(self, run_id: str) -> AgentRunState:
        events = self.events(run_id)
        if not events:
            raise CorruptRunError(f"run does not exist: {run_id}")
        sequences = [event.sequence for event in events]
        if len(sequences) != len(set(sequences)):
            raise CorruptRunError("duplicate event sequence")
        state: AgentRunState | None = None
        try:
            for event in events:
                state = reduce_event(state, event)
        except (InvalidTransitionError, ValueError) as exc:
            raise CorruptRunError("event replay failed") from exc
        if state is None:
            raise CorruptRunError("event replay produced no state")
        return state

    def _reduce(self, state: AgentRunState | None, event: RunEvent) -> AgentRunState:
        try:
            return reduce_event(state, event)
        except (InvalidTransitionError, ValueError):
            raise

    def _insert_event(self, event: RunEvent) -> None:
        self._connection.execute(
            """INSERT INTO run_events(
                   run_id, sequence, event_id, event_kind, payload_json, payload_sha256, created_at_utc
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event.run_id,
                event.sequence,
                event.event_id,
                event.event_kind,
                canonical_json(event.payload),
                event.payload_sha256,
                event.created_at_utc,
            ),
        )

    def _write_snapshot(self, state: AgentRunState) -> None:
        raw_state = state.to_dict()
        self._connection.execute(
            """INSERT INTO run_snapshots(run_id, sequence, state_json, state_sha256)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET
                   sequence = excluded.sequence,
                   state_json = excluded.state_json,
                   state_sha256 = excluded.state_sha256""",
            (state.run_id, state.last_event_sequence, canonical_json(raw_state), sha256_json(raw_state)),
        )

    @staticmethod
    def _event_from_row(row: tuple[object, ...]) -> RunEvent:
        event_id, run_id, sequence, event_kind, payload_json, payload_sha256, created_at_utc = row
        try:
            payload = json.loads(cast(str, payload_json))
            if not isinstance(payload, dict):
                raise ValueError("event payload is not an object")
            return RunEvent(
                event_id=cast(str, event_id),
                run_id=cast(str, run_id),
                sequence=cast(int, sequence),
                event_kind=cast(str, event_kind),
                payload=payload,
                payload_sha256=cast(str, payload_sha256),
                created_at_utc=cast(str, created_at_utc),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CorruptRunError("event payload or hash is invalid") from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_runtime_root(
    root: Path,
    *,
    allowed_runtime_root: Path | None = None,
    platform: str | None = None,
) -> Path:
    """Validate Windows E: or a narrowly configured POSIX runtime subtree."""

    selected_platform = os.name if platform is None else platform
    candidate = Path(root)
    if selected_platform == "nt":
        if PureWindowsPath(str(candidate)).drive.upper() != "E:":
            raise ValueError("AgentRunStore root must be on the E: drive")
        return candidate
    if allowed_runtime_root is None:
        raise ValueError("POSIX AgentRunStore requires a configured runtime root")
    if platform is not None and os.name != "posix":
        pure_candidate = PurePosixPath(str(candidate).replace("\\", "/"))
        pure_allowed = PurePosixPath(str(allowed_runtime_root).replace("\\", "/"))
        try:
            pure_candidate.relative_to(pure_allowed)
        except ValueError as exc:
            raise ValueError(
                "AgentRunStore root must remain under the configured runtime root"
            ) from exc
        return candidate
    resolved_candidate = candidate.expanduser().resolve()
    resolved_allowed = Path(allowed_runtime_root).expanduser().resolve()
    try:
        resolved_candidate.relative_to(resolved_allowed)
    except ValueError as exc:
        raise ValueError(
            "AgentRunStore root must remain under the configured runtime root"
        ) from exc
    return resolved_candidate


def _redacted_mapping(value: Mapping[str, object]) -> dict[str, object]:
    redacted = _redact(_durable_plain(value))
    if not isinstance(redacted, dict):
        raise ValueError("redacted durable payload must remain an object")
    return redacted


def _redacted_tool_result(result: ToolResult) -> ToolResult:
    output: Mapping[str, Any] | None = None
    if result.output is not None:
        redacted_output = _redact(_durable_plain(result.output))
        if not isinstance(redacted_output, dict):
            raise ValueError("redacted tool output must remain an object")
        output = redacted_output
    redacted_error = _redact(result.error_type)
    redacted_summary = _redact(result.redacted_summary)
    if redacted_error is not None and not isinstance(redacted_error, str):
        raise ValueError("redacted error type must remain a string")
    if not isinstance(redacted_summary, str):
        raise ValueError("redacted result summary must remain a string")
    return ToolResult(
        status=result.status,
        output=output,
        output_sha256=sha256_json(output) if output is not None else None,
        idempotency_key=result.idempotency_key,
        error_type=redacted_error,
        redacted_summary=redacted_summary,
    )


def _durable_tool_result_digest(result: ToolResult) -> str:
    return sha256_json(
        {
            "status": result.status,
            "output": result.output,
            "output_sha256": result.output_sha256,
            "idempotency_key": result.idempotency_key,
            "error_type": result.error_type,
            "redacted_summary": result.redacted_summary,
        }
    )


def _durable_plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _durable_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_durable_plain(item) for item in value]
    return value


def _execution_attempt_from_row(row: tuple[object, ...]) -> ToolExecutionAttempt:
    values = list(row)
    if len(values) != 13:
        raise CorruptRunError("tool execution attempt row is malformed")
    run_id, step_index, idempotency_key, tool_name, tool_version, args_sha256 = values[:6]
    attempt_id, owner_id, status, started_at, updated_at, reason, result_digest = values[6:]
    if not all(
        isinstance(value, str) and value
        for value in (
            run_id,
            idempotency_key,
            tool_name,
            tool_version,
            args_sha256,
            attempt_id,
            owner_id,
            status,
            started_at,
            updated_at,
        )
    ):
        raise CorruptRunError("tool execution attempt fields are malformed")
    if not isinstance(step_index, int) or isinstance(step_index, bool):
        raise CorruptRunError("tool execution attempt step is malformed")
    if not all(value is None or isinstance(value, str) for value in (reason, result_digest)):
        raise CorruptRunError("tool execution attempt outcome is malformed")
    return ToolExecutionAttempt(
        run_id=cast(str, run_id),
        step_index=step_index,
        idempotency_key=cast(str, idempotency_key),
        tool_name=cast(str, tool_name),
        tool_version=cast(str, tool_version),
        args_sha256=cast(str, args_sha256),
        attempt_id=cast(str, attempt_id),
        owner_id=cast(str, owner_id),
        status=cast(str, status),
        started_at_utc=cast(str, started_at),
        updated_at_utc=cast(str, updated_at),
        reason=cast(str | None, reason),
        result_digest=cast(str | None, result_digest),
    )
