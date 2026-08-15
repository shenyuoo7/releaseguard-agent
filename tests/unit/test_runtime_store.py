import json
import sqlite3
from pathlib import Path

import pytest

from releaseguard_agent.runtime.models import (
    AgentRunState,
    RunBudget,
    RunEvent,
    canonical_json,
    run_event_digest,
    sha256_json,
)
from releaseguard_agent.runtime.store import (
    AgentRunStore,
    CorruptRunError,
    StaleSequenceError,
    validate_runtime_root,
)
from releaseguard_agent.runtime.tools import ToolCall, ToolResult


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_state() -> AgentRunState:
    return AgentRunState.created(
        run_id="run-store-1",
        task_kind="REVIEW",
        project_root=PROJECT_ROOT,
        budget=RunBudget(max_steps=2, max_tool_calls=2, max_retries=1),
    )


def test_store_replays_and_rejects_stale_sequence(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "runtime")
    created = store.create_run(make_state())
    assert created.sequence == 0

    plan = store.append(created.run_id, 0, "PLAN_PROPOSED", {"plan_digest": "p"})
    assert plan.sequence == 1
    with pytest.raises(StaleSequenceError):
        store.append(created.run_id, 0, "TOOL_REQUESTED", {"idempotency_key": "k", "tool_name": "scan"})

    assert store.load_state(created.run_id).status == "RUNNING"
    assert [item.event_kind for item in store.events(created.run_id)] == [
        "RUN_CREATED",
        "PLAN_PROPOSED",
    ]
    store.close()


def test_store_reopens_and_replays_persisted_events(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    store = AgentRunStore(root)
    created = store.create_run(make_state())
    store.append(created.run_id, 0, "PLAN_PROPOSED", {"plan_digest": "p"})
    store.close()

    reopened = AgentRunStore(root)
    assert reopened.load_state(created.run_id).plan_digest == "p"
    assert (root / "agent_runs.sqlite3").exists()
    reopened.close()


def test_store_rejects_direct_secret_tool_request_before_durable_persistence(
    tmp_path: Path,
) -> None:
    """A bypass of ToolCall.create cannot put opaque secrets in event storage."""

    store = AgentRunStore(tmp_path / "secret-request")
    created = store.create_run(make_state())
    store.append(created.run_id, 0, "PLAN_PROPOSED", {"plan_digest": "plan"})
    opaque_secret = "opaque-value-that-is-not-a-secret-pattern"
    arguments = {
        "project_path": str(PROJECT_ROOT),
        "github_token": opaque_secret,
    }
    canonical_args = canonical_json(arguments)

    with pytest.raises(ValueError, match="sensitive"):
        store.append(
            created.run_id,
            1,
            "TOOL_REQUESTED",
            {
                "tool_name": "scan_project",
                "tool_version": "1",
                "canonical_args": canonical_args,
                "args_sha256": sha256_json(arguments),
                "step_index": 0,
                "idempotency_key": "direct-secret-request",
            },
        )

    persisted = canonical_json([event.to_dict() for event in store.events(created.run_id)])
    assert opaque_secret not in persisted
    assert store.load_state(created.run_id).last_event_sequence == 1
    store.close()


def test_store_rejects_direct_secret_request_before_creating_event_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Secret rejection must precede redaction, event hash, and SQLite work."""

    store = AgentRunStore(tmp_path / "pre-envelope-secret-request")
    created = store.create_run(make_state())
    store.append(created.run_id, 0, "PLAN_PROPOSED", {"plan_digest": "plan"})
    arguments = {
        "project_path": str(PROJECT_ROOT),
        "github_token": "opaque-value-that-is-not-a-secret-pattern",
    }
    created_envelope = False

    def fail_if_envelope_is_created(
        cls: type[RunEvent],
        **_: object,
    ) -> RunEvent:
        nonlocal created_envelope
        created_envelope = True
        raise AssertionError("TOOL_REQUESTED secret reached RunEvent.create")

    monkeypatch.setattr(
        RunEvent,
        "create",
        classmethod(fail_if_envelope_is_created),
    )

    with pytest.raises(ValueError, match="sensitive"):
        store.append(
            created.run_id,
            1,
            "TOOL_REQUESTED",
            {
                "tool_name": "scan_project",
                "tool_version": "1",
                "canonical_args": canonical_json(arguments),
                "args_sha256": sha256_json(arguments),
                "step_index": 0,
                "idempotency_key": "pre-envelope-secret-request",
            },
        )

    assert created_envelope is False
    assert store.load_state(created.run_id).last_event_sequence == 1
    store.close()


def test_store_rejects_payload_hash_corruption(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    store = AgentRunStore(root)
    created = store.create_run(make_state())
    store.append(created.run_id, 0, "PLAN_PROPOSED", {"plan_digest": "p"})
    store.close()

    connection = sqlite3.connect(root / "agent_runs.sqlite3")
    connection.execute(
        "UPDATE run_events SET payload_json = ? WHERE run_id = ? AND sequence = ?",
        (json.dumps({"plan_digest": "tampered"}), created.run_id, 1),
    )
    connection.commit()
    connection.close()

    corrupted = AgentRunStore(root)
    with pytest.raises(CorruptRunError, match="payload"):
        corrupted.load_state(created.run_id)
    corrupted.close()


def test_store_rolls_back_invalid_event_before_commit(tmp_path: Path) -> None:
    """An invalid reducer transition must not expose a partially appended event."""

    store = AgentRunStore(tmp_path / "runtime")
    created = store.create_run(make_state())

    with pytest.raises(ValueError):
        store.append(created.run_id, 0, "TOOL_STARTED", {"idempotency_key": "tool-1"})

    assert [event.sequence for event in store.events(created.run_id)] == [0]
    assert store.load_state(created.run_id).last_event_sequence == 0
    store.close()


def test_store_rolls_back_when_database_aborts_mid_transaction(tmp_path: Path) -> None:
    """A database failure after event insertion leaves the prior snapshot visible."""

    root = tmp_path / "runtime"
    store = AgentRunStore(root)
    created = store.create_run(make_state())
    store.close()

    connection = sqlite3.connect(root / "agent_runs.sqlite3")
    connection.execute(
        """
        CREATE TRIGGER abort_snapshot_write
        BEFORE UPDATE ON run_snapshots
        WHEN NEW.sequence = 1
        BEGIN
            SELECT RAISE(ABORT, 'injected snapshot failure');
        END
        """
    )
    connection.commit()
    connection.close()

    failed = AgentRunStore(root)
    with pytest.raises(sqlite3.DatabaseError, match="injected snapshot failure"):
        failed.append(created.run_id, 0, "PLAN_PROPOSED", {"plan_digest": "p"})
    assert [event.sequence for event in failed.events(created.run_id)] == [0]
    assert failed.load_state(created.run_id).last_event_sequence == 0
    failed.close()


def test_store_fails_closed_when_event_is_deleted(tmp_path: Path) -> None:
    """A missing sequence must be detected instead of replaying a shortened history."""

    root = tmp_path / "runtime"
    store = AgentRunStore(root)
    created = store.create_run(make_state())
    store.append(created.run_id, 0, "PLAN_PROPOSED", {"plan_digest": "p"})
    store.close()

    connection = sqlite3.connect(root / "agent_runs.sqlite3")
    connection.execute("DELETE FROM run_events WHERE run_id = ? AND sequence = 0", (created.run_id,))
    connection.commit()
    connection.close()

    corrupted = AgentRunStore(root)
    with pytest.raises(CorruptRunError, match="replay"):
        corrupted.load_state(created.run_id)
    corrupted.close()


def test_store_fails_closed_when_duplicate_sequence_is_injected(tmp_path: Path) -> None:
    """A duplicate sequence in a corrupted table cannot be replayed as valid history."""

    root = tmp_path / "runtime"
    store = AgentRunStore(root)
    created = store.create_run(make_state())
    store.close()

    connection = sqlite3.connect(root / "agent_runs.sqlite3")
    connection.execute("DROP TABLE run_events")
    connection.execute(
        """
        CREATE TABLE run_events(
            run_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            event_kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        )
        """
    )
    event = created
    connection.executemany(
        "INSERT INTO run_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                created.run_id,
                0,
                event.event_id,
                event.event_kind,
                json.dumps({"state": make_state().to_dict()}),
                event.payload_sha256,
                event.created_at_utc,
            ),
            (
                created.run_id,
                0,
                "duplicate-event",
                event.event_kind,
                json.dumps({"state": make_state().to_dict()}),
                run_event_digest(
                    event_id="duplicate-event",
                    run_id=created.run_id,
                    sequence=0,
                    event_kind=event.event_kind,
                    payload={"state": make_state().to_dict()},
                    created_at_utc=event.created_at_utc,
                ),
                event.created_at_utc,
            ),
        ],
    )
    connection.commit()
    connection.close()

    corrupted = AgentRunStore(root)
    with pytest.raises(CorruptRunError, match="duplicate"):
        corrupted.load_state(created.run_id)
    corrupted.close()


def test_store_does_not_append_over_a_corrupt_snapshot(tmp_path: Path) -> None:
    """CAS must validate the persisted snapshot before it can advance a run."""

    root = tmp_path / "runtime"
    store = AgentRunStore(root)
    created = store.create_run(make_state())
    store.append(created.run_id, 0, "PLAN_PROPOSED", {"plan_digest": "p"})
    store.close()

    connection = sqlite3.connect(root / "agent_runs.sqlite3")
    connection.execute(
        "UPDATE run_snapshots SET state_json = ? WHERE run_id = ?",
        (json.dumps({"tampered": True}), created.run_id),
    )
    connection.commit()
    connection.close()

    corrupted = AgentRunStore(root)
    with pytest.raises(CorruptRunError, match="snapshot"):
        corrupted.append(
            created.run_id,
            1,
            "TOOL_REQUESTED",
            {"idempotency_key": "tool-1", "tool_name": "scan"},
        )
    assert [event.sequence for event in corrupted.events(created.run_id)] == [0, 1]
    corrupted.close()


def test_store_rejects_non_e_drive_root_before_creating_files() -> None:
    """Runtime persistence is restricted to an E-drive root."""

    with pytest.raises(ValueError, match="E: drive"):
        AgentRunStore(Path(r"C:\ReleaseGuard_runtime_test_task2"))


def test_runtime_root_policy_accepts_only_e_drive_on_windows() -> None:
    assert validate_runtime_root(
        Path(r"E:\ReleaseGuard\.runtime\agent_runs"), platform="nt"
    ) == Path(r"E:\ReleaseGuard\.runtime\agent_runs")

    with pytest.raises(ValueError, match="E: drive"):
        validate_runtime_root(
            Path(r"C:\ReleaseGuard\.runtime\agent_runs"), platform="nt"
        )


def test_runtime_root_policy_on_posix_requires_the_configured_root() -> None:
    allowed = Path("/workspace/releaseguard/.runtime")
    runtime = allowed / "agent_runs"

    assert validate_runtime_root(
        runtime,
        allowed_runtime_root=allowed,
        platform="posix",
    ) == runtime

    with pytest.raises(ValueError, match="configured runtime root"):
        validate_runtime_root(
            Path("/tmp/arbitrary-releaseguard-runtime"),
            allowed_runtime_root=allowed,
            platform="posix",
        )


def test_store_reserves_one_durable_execution_attempt_across_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "attempt-lease"
    call = ToolCall.create(
        tool_name="scan_project",
        tool_version="1",
        args={"project_path": str(PROJECT_ROOT)},
        run_id="run-attempt",
        step_index=0,
        idempotency_key="logical-operation-1",
    )
    store = AgentRunStore(root)
    first = store.start_tool_attempt(call, owner_id="worker-one")
    duplicate = store.start_tool_attempt(call, owner_id="worker-two")

    assert first.status == "STARTED"
    assert duplicate == first
    assert duplicate.owner_id == "worker-one"
    store.close()

    reopened = AgentRunStore(root)
    assert reopened.load_tool_attempt(call) == first
    reopened.mark_tool_attempt_ambiguous(
        call,
        reason="execution_timeout_ambiguous",
    )
    assert reopened.load_tool_attempt(call).status == "AMBIGUOUS"
    reopened.close()


def test_store_atomically_records_a_result_and_completes_its_attempt(
    tmp_path: Path,
) -> None:
    """A committed result must not leave its exact durable attempt STARTED."""

    store = AgentRunStore(tmp_path / "atomic-attempt")
    call = ToolCall.create(
        tool_name="scan_project",
        tool_version="1",
        args={"project_path": str(PROJECT_ROOT)},
        run_id="run-atomic-attempt",
        step_index=0,
        idempotency_key="logical-operation-1",
    )
    store.start_tool_attempt(call, owner_id="worker-one")
    result = ToolResult(
        status="completed",
        output={"release_allowed": True},
        output_sha256="922fc65011efc2a3866ffa3b2bb68de0bea552a7361266e9cca82a82a4b9b970",
        idempotency_key=call.idempotency_key,
        error_type=None,
        redacted_summary="Tool completed.",
    )

    stored = store.record_completed_tool_result(call, result)

    attempt = store.load_tool_attempt(call)
    assert stored == result
    assert store.load_tool_result(call) == result
    assert attempt is not None
    assert attempt.status == "COMPLETED"
    assert attempt.result_digest
    store.close()


def test_store_reconciles_a_historical_result_before_attempt_completion(
    tmp_path: Path,
) -> None:
    """Restart recovery converges an old split write without reinvoking a tool."""

    store = AgentRunStore(tmp_path / "historical-split-attempt")
    call = ToolCall.create(
        tool_name="scan_project",
        tool_version="1",
        args={"project_path": str(PROJECT_ROOT)},
        run_id="run-historical-split",
        step_index=0,
        idempotency_key="logical-operation-1",
    )
    store.start_tool_attempt(call, owner_id="worker-one")
    result = ToolResult(
        status="completed",
        output={"release_allowed": True},
        output_sha256="922fc65011efc2a3866ffa3b2bb68de0bea552a7361266e9cca82a82a4b9b970",
        idempotency_key=call.idempotency_key,
        error_type=None,
        redacted_summary="Tool completed.",
    )
    store.record_tool_result(call, result)

    reconciled = store.reconcile_completed_tool_attempt(call)

    attempt = store.load_tool_attempt(call)
    assert reconciled == result
    assert attempt is not None
    assert attempt.status == "COMPLETED"
    assert attempt.result_digest
    store.close()
