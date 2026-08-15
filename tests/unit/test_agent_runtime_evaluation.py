import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest

from releaseguard_agent.agent_tools import build_release_tool_registry
from releaseguard_agent.evaluation import EvaluationRunner
from releaseguard_agent.observability import ExecutionTracer
from releaseguard_agent.runtime import (
    LoopController,
    LoopRequest,
    RunBudget,
    RunEvent,
    ToolExecutionContext,
    ToolRegistry,
    ToolSpec,
    canonical_json,
    run_event_digest,
    sha256_json,
)
from releaseguard_agent.runtime.store import AgentRunStore, CorruptRunError
from releaseguard_agent.runtime.models import ApprovalGrant, ApprovalRequest
from releaseguard_agent.services.agent_workflow_service import ReleaseAgentWorkflowService
from releaseguard_agent.services.release_review_service import ReleaseReviewService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET = PROJECT_ROOT / "evals" / "datasets" / "agent_runtime_cases.json"
SAMPLE = PROJECT_ROOT / "sample_projects" / "clean_python_project"
REQUIRED_METRICS = {
    "tool_call_validity",
    "guardrail_precision",
    "resume_success_rate",
    "duplicate_side_effect_rate",
    "hitl_gate_recall",
    "loop_termination_rate",
    "average_tool_calls",
    "average_retries",
}


def test_runtime_dataset_exercises_every_required_failure_class() -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))

    assert {case["scenario"] for case in payload["runtime_cases"]} == {
        "positive",
        "malformed",
        "crash",
        "stale_approval",
        "prompt_injection",
        "safe_guardrail_false_positive",
        "multi_guardrail_denial",
        "retry",
        "budget_exhaustion",
    }
    assert all(case["expected_events"] for case in payload["runtime_cases"])
    assert all(case["expected_final_status"] for case in payload["runtime_cases"])


def test_runtime_metrics_use_nonzero_explicit_denominators_and_event_evidence() -> None:
    result = EvaluationRunner(PROJECT_ROOT).run(DATASET)

    runtime = result.details["runtime"]
    assert set(result.metrics) == REQUIRED_METRICS
    assert set(runtime["denominators"]) == REQUIRED_METRICS
    assert all(value > 0 for value in runtime["denominators"].values())
    assert result.metrics["duplicate_side_effect_rate"] == 0.0
    assert runtime["denominators"]["guardrail_precision"] == 5
    assert result.metrics["guardrail_precision"] == pytest.approx(3 / 5)
    assert result.metrics["resume_success_rate"] == 1.0
    assert result.metrics["hitl_gate_recall"] == 1.0
    assert result.metrics["loop_termination_rate"] == 1.0
    assert result.passed is True
    assert runtime["thresholds"]["guardrail_precision"] == 0.6
    assert all(runtime["threshold_results"].values())
    assert runtime["limitations"]
    assert all(case["observed_events"] for case in runtime["cases"])
    assert all(case["matched"] for case in runtime["cases"])
    assert all(
        evidence["event_sequences"]
        for case in runtime["cases"]
        for evidence in case["metric_evidence"]
    )
    multi = next(
        case
        for case in runtime["cases"]
        if case["scenario"] == "multi_guardrail_denial"
    )
    precision = next(
        item
        for item in multi["metric_evidence"]
        if item["metric"] == "guardrail_precision"
    )
    assert precision["denominator"] == 2
    assert precision["numerator"] == 1
    assert multi["guardrail_denial_evidence"] == [
        {
            "event_sequence": multi["guardrail_denial_evidence"][0][
                "event_sequence"
            ],
            "actual_error_type": "path_not_allowed",
            "expected_error_type": "path_not_allowed",
            "unsafe_label": True,
            "reason_matches": True,
        },
        {
            "event_sequence": multi["guardrail_denial_evidence"][1][
                "event_sequence"
            ],
            "actual_error_type": "path_not_allowed",
            "expected_error_type": None,
            "unsafe_label": False,
            "reason_matches": False,
        },
    ]


def test_runtime_replay_matches_digest_routes_and_idempotency_outcomes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "replay"
    store = AgentRunStore(root)
    controller = LoopController(
        store,
        build_release_tool_registry(allowed_roots=(SAMPLE,)),
        ReleaseAgentWorkflowService(),
        ExecutionTracer(run_id="runtime-replay"),
    )
    completed = controller.run(
        LoopRequest(
            project_path=SAMPLE,
            task_kind="REVIEW",
            budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
        )
    )

    replay = EvaluationRunner(PROJECT_ROOT).replay_runtime_run(
        store, completed.run_id
    )

    assert replay.decision_digest == completed.state.decision_digest
    assert replay.route_history == completed.route_history
    assert replay.final_status == completed.status
    assert replay.idempotency_outcomes == (
        (
            f"{completed.run_id}:0:scan_project",
            "completed",
            next(
                event.payload["output_sha256"]
                for event in store.events(completed.run_id)
                if event.event_kind == "TOOL_COMPLETED"
            ),
        ),
    )
    assert completed.trace_path is not None
    trace = json.loads(completed.trace_path.read_text(encoding="utf-8"))
    requested = next(
        event
        for event in trace["events"]
        if event.get("event_kind") == "TOOL_REQUESTED"
    )
    assert requested["run_id"] == completed.run_id
    assert requested["step_index"] == 0
    assert requested["idempotency_key"] == (
        f"{completed.run_id}:0:scan_project"
    )
    guardrail = next(
        event for event in trace["events"] if event["kind"] == "guardrail"
    )
    assert guardrail["guardrail_decision"] == "ALLOW"
    checkpoint = next(
        event
        for event in trace["events"]
        if event.get("event_kind") == "CHECKPOINT_COMMITTED"
    )
    assert checkpoint["checkpoint_sequence"] == checkpoint["event_sequence"]
    store.close()


def test_runtime_replay_rejects_modified_payload_hash(tmp_path: Path) -> None:
    root = tmp_path / "corrupt-replay"
    store = AgentRunStore(root)
    controller = LoopController(
        store,
        build_release_tool_registry(allowed_roots=(SAMPLE,)),
        ReleaseAgentWorkflowService(),
        None,
    )
    completed = controller.run(
        LoopRequest(
            project_path=SAMPLE,
            task_kind="REVIEW",
            budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
        )
    )
    store.close()

    with sqlite3.connect(root / "agent_runs.sqlite3") as connection:
        payload_json = connection.execute(
            "SELECT payload_json FROM run_events WHERE run_id = ? AND sequence = 1",
            (completed.run_id,),
        ).fetchone()[0]
        payload = json.loads(payload_json)
        payload["plan_digest"] = "modified-without-hash-update"
        connection.execute(
            "UPDATE run_events SET payload_json = ? WHERE run_id = ? AND sequence = 1",
            (json.dumps(payload, sort_keys=True), completed.run_id),
        )
        connection.commit()

    reopened = AgentRunStore(root)
    with pytest.raises(CorruptRunError, match="payload or hash"):
        EvaluationRunner(PROJECT_ROOT).replay_runtime_run(
            reopened, completed.run_id
        )
    reopened.close()


def test_runtime_replay_rejects_missing_execution_failure_ledger(
    tmp_path: Path,
) -> None:
    root = tmp_path / "missing-failure-ledger"
    registry = _scan_registry(fail=True)
    store = AgentRunStore(root)
    controller = LoopController(
        store,
        registry,
        ReleaseAgentWorkflowService(),
        None,
    )
    failed = controller.run(
        LoopRequest(
            project_path=SAMPLE,
            task_kind="REVIEW",
            budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
        )
    )
    assert failed.status == "FAILED"
    failure = next(
        event
        for event in store.events(failed.run_id)
        if event.event_kind == "TOOL_FAILED"
    )
    assert failure.payload["failure_phase"] == "execution"
    store.close()

    with sqlite3.connect(root / "agent_runs.sqlite3") as connection:
        connection.execute(
            "DELETE FROM tool_results WHERE run_id = ?",
            (failed.run_id,),
        )
        connection.commit()

    reopened = AgentRunStore(root)
    with pytest.raises(CorruptRunError, match="failure.*ledger"):
        EvaluationRunner(PROJECT_ROOT).replay_runtime_run(
            reopened, failed.run_id
        )
    reopened.close()


def test_runtime_replay_accepts_paused_ambiguous_execution_without_ledger(
    tmp_path: Path,
) -> None:
    root = tmp_path / "uncertain-replay"
    store = AgentRunStore(root)
    crashing = _CrashAfterExecutionController(
        store,
        _scan_registry(),
        ReleaseAgentWorkflowService(),
        None,
    )
    with pytest.raises(_InjectedCrash):
        crashing.run(
            LoopRequest(
                project_path=SAMPLE,
                task_kind="REVIEW",
                budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
            )
        )
    run_id = crashing.last_run_id
    store.close()

    reopened = AgentRunStore(root)
    result = LoopController(
        reopened,
        _scan_registry(),
        ReleaseAgentWorkflowService(),
        None,
    ).resume(run_id)
    assert result.metrics["stop_reason"] == "execution_ambiguous"

    replay = EvaluationRunner(PROJECT_ROOT).replay_runtime_run(reopened, run_id)
    assert replay.final_status == "PAUSED"
    assert replay.idempotency_outcomes[0][1:] == (
        "ambiguous",
        "execution_attempt_unresolved",
    )
    reopened.close()


def test_runtime_replay_rejects_ledger_row_for_uncertain_execution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "uncertain-tamper"
    store = AgentRunStore(root)
    crashing = _CrashAfterExecutionController(
        store,
        _scan_registry(),
        ReleaseAgentWorkflowService(),
        None,
    )
    with pytest.raises(_InjectedCrash):
        crashing.run(
            LoopRequest(
                project_path=SAMPLE,
                task_kind="REVIEW",
                budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
            )
        )
    run_id = crashing.last_run_id
    store.close()
    reopened = AgentRunStore(root)
    LoopController(
        reopened,
        _scan_registry(),
        ReleaseAgentWorkflowService(),
        None,
    ).resume(run_id)
    reopened.close()

    with sqlite3.connect(root / "agent_runs.sqlite3") as connection:
        connection.execute(
            """INSERT INTO tool_results(
                   run_id, step_index, idempotency_key, tool_name,
                   tool_version, args_sha256, status, output_json,
                   output_sha256, error_type, redacted_summary
               ) SELECT run_id, 0, ?, 'scan_project', '1',
                        json_extract(payload_json, '$.args_sha256'),
                        'error', NULL, NULL, 'uncertain_tool_execution',
                        'tampered'
               FROM run_events
               WHERE run_id = ? AND event_kind = 'TOOL_REQUESTED'""",
            (f"{run_id}:0:scan_project", run_id),
        )
        connection.commit()

    tampered = AgentRunStore(root)
    with pytest.raises(CorruptRunError, match="ambiguous.*ledger"):
        EvaluationRunner(PROJECT_ROOT).replay_runtime_run(tampered, run_id)
    tampered.close()


def test_hitl_approval_id_is_durable_validated_and_traced(tmp_path: Path) -> None:
    root = tmp_path / "approval-trace"
    tracer = ExecutionTracer(run_id="approval-trace")
    store = AgentRunStore(root)
    controller = LoopController(
        store,
        _scan_registry(required_approval_scope="approved_change"),
        ReleaseAgentWorkflowService(),
        tracer,
    )
    waiting = controller.run(
        LoopRequest(
            project_path=SAMPLE,
            task_kind="REVIEW",
            budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
        )
    )

    approval_id = waiting.state.pending_approval_id
    assert approval_id is not None
    requested = next(
        event
        for event in store.events(waiting.run_id)
        if event.event_kind == "TOOL_REQUESTED"
    )
    assert requested.payload["approval_id"] == approval_id
    waiting_trace = json.loads(waiting.trace_path.read_text(encoding="utf-8"))
    assert any(
        event.get("approval_id") == approval_id
        and event.get("guardrail_decision") == "REQUIRE_HITL"
        for event in waiting_trace["events"]
    )

    still_waiting = controller.resume(
        waiting.run_id,
        approval={
            "approval_id": f"stale-{approval_id}",
            "approved_scopes": ["approved_change"],
        },
    )
    assert still_waiting.status == "WAITING_HITL"
    completed = controller.resume(
        waiting.run_id,
        approval=_approved_grant(store, waiting.run_id),
    )

    assert completed.status == "COMPLETED"
    assert completed.state.pending_approval_id is None
    started = next(
        event
        for event in store.events(waiting.run_id)
        if event.event_kind == "TOOL_STARTED"
    )
    assert started.payload["approval_id"] == approval_id
    final_trace = json.loads(completed.trace_path.read_text(encoding="utf-8"))
    assert any(
        event.get("approval_id") == approval_id
        and event.get("guardrail_decision") == "ALLOW"
        for event in final_trace["events"]
    )
    store.close()


def test_accepted_approval_survives_crash_and_executes_exactly_once(
    tmp_path: Path,
) -> None:
    root = tmp_path / "accepted-approval-crash"
    initial_calls = {"count": 0}
    store = AgentRunStore(root)
    controller = LoopController(
        store,
        _scan_registry(
            required_approval_scope="approved_change",
            calls=initial_calls,
        ),
        ReleaseAgentWorkflowService(),
        None,
    )
    waiting = controller.run(
        LoopRequest(
            project_path=SAMPLE,
            task_kind="REVIEW",
            budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
        )
    )
    approval_id = waiting.state.pending_approval_id
    assert approval_id is not None
    crashing = _CrashAfterApprovalDecidedController(
        store,
        _scan_registry(
            required_approval_scope="approved_change",
            calls=initial_calls,
        ),
        ReleaseAgentWorkflowService(),
        None,
    )
    with pytest.raises(_InjectedCrash):
        crashing.resume(
            waiting.run_id,
            approval=_approved_grant(store, waiting.run_id),
        )

    accepted = store.events(waiting.run_id)[-1]
    assert accepted.event_kind == "APPROVAL_DECIDED"
    assert accepted.payload["grant"]["approval_id"] == approval_id
    assert accepted.payload["grant"]["scope"] == "approved_change"
    assert accepted.payload["grant"]["idempotency_key"] == (
        f"{waiting.run_id}:0:scan_project"
    )
    assert initial_calls["count"] == 0
    store.close()

    resumed_calls = {"count": 0}
    reopened = AgentRunStore(root)
    completed = LoopController(
        reopened,
        _scan_registry(
            required_approval_scope="approved_change",
            calls=resumed_calls,
        ),
        ReleaseAgentWorkflowService(),
        None,
    ).resume(waiting.run_id)

    assert completed.status == "COMPLETED"
    assert resumed_calls["count"] == 1
    event_count = len(reopened.events(waiting.run_id))
    reopened.close()

    replay_calls = {"count": 0}
    terminal_store = AgentRunStore(root)
    terminal = LoopController(
        terminal_store,
        _scan_registry(
            required_approval_scope="approved_change",
            calls=replay_calls,
        ),
        ReleaseAgentWorkflowService(),
        None,
    ).resume(
        waiting.run_id,
        approval=_approved_grant(terminal_store, waiting.run_id),
    )
    assert terminal.status == "COMPLETED"
    assert replay_calls["count"] == 0
    assert len(terminal_store.events(waiting.run_id)) == event_count
    terminal_store.close()


def test_retryable_approved_call_requires_a_new_exact_grant(
    tmp_path: Path,
) -> None:
    calls = {"count": 0}
    store = AgentRunStore(tmp_path / "approval-retry")
    controller = LoopController(
        store,
        _scan_registry(
            required_approval_scope="approved_change",
            fail_once=True,
            calls=calls,
        ),
        ReleaseAgentWorkflowService(),
        None,
    )
    waiting = controller.run(
        LoopRequest(
            project_path=SAMPLE,
            task_kind="REVIEW",
            budget=RunBudget(max_steps=2, max_tool_calls=2, max_retries=1),
        )
    )
    first_approval_id = waiting.state.pending_approval_id
    assert first_approval_id is not None

    retry_checkpointed = controller.resume(
        waiting.run_id,
        approval=_approved_grant(store, waiting.run_id),
    )
    assert retry_checkpointed.status == "RUNNING"
    retry_waiting = controller.resume(waiting.run_id)

    assert retry_waiting.status == "WAITING_HITL"
    assert retry_waiting.state.pending_approval_id != first_approval_id
    assert calls["count"] == 1
    requests = [
        event
        for event in store.events(waiting.run_id)
        if event.event_kind == "APPROVAL_REQUESTED"
    ]
    assert len(requests) == 2
    assert requests[0].payload["request"]["idempotency_key"] != (
        requests[1].payload["request"]["idempotency_key"]
    )

    completed = controller.resume(
        waiting.run_id,
        approval=_approved_grant(store, waiting.run_id),
    )
    assert completed.status == "COMPLETED"
    assert calls["count"] == 2
    store.close()


def test_mismatched_approval_decision_event_fails_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "mismatched-approval-event"
    store = AgentRunStore(root)
    waiting = LoopController(
        store,
        _scan_registry(required_approval_scope="approved_change"),
        ReleaseAgentWorkflowService(),
        None,
    ).run(
        LoopRequest(
            project_path=SAMPLE,
            task_kind="REVIEW",
            budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
        )
    )
    state = store.load_state(waiting.run_id)
    malformed_grant = _approved_grant(store, waiting.run_id).to_dict()
    malformed_grant["approval_id"] = "approval:mismatched"
    store.close()
    payload = {"grant": malformed_grant}
    _insert_event_row(root, waiting.run_id, state.last_event_sequence + 1, payload)

    tampered = AgentRunStore(root)
    with pytest.raises(CorruptRunError, match="event replay"):
        tampered.load_state(waiting.run_id)
    tampered.close()


def test_duplicate_approval_decision_event_fails_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "duplicate-approval-event"
    store = AgentRunStore(root)
    waiting = LoopController(
        store,
        _scan_registry(required_approval_scope="approved_change"),
        ReleaseAgentWorkflowService(),
        None,
    ).run(
        LoopRequest(
            project_path=SAMPLE,
            task_kind="REVIEW",
            budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
        )
    )
    approval_id = waiting.state.pending_approval_id
    assert approval_id is not None
    crashing = _CrashAfterApprovalDecidedController(
        store,
        _scan_registry(required_approval_scope="approved_change"),
        ReleaseAgentWorkflowService(),
        None,
    )
    with pytest.raises(_InjectedCrash):
        crashing.resume(
            waiting.run_id,
            approval=_approved_grant(store, waiting.run_id),
        )
    accepted = store.events(waiting.run_id)[-1]
    store.close()
    _insert_event_row(
        root,
        waiting.run_id,
        accepted.sequence + 1,
        dict(accepted.payload),
    )

    tampered = AgentRunStore(root)
    with pytest.raises(CorruptRunError, match="event replay"):
        tampered.load_state(waiting.run_id)
    tampered.close()


@pytest.mark.parametrize(
    "crash_boundary",
    ["created", "plan"],
)
def test_early_approval_data_cannot_preauthorize_future_tool(
    tmp_path: Path,
    crash_boundary: str,
) -> None:
    controller_type = (
        _CrashAfterRunCreatedController
        if crash_boundary == "created"
        else _CrashAfterPlanController
    )
    root = tmp_path / crash_boundary
    store = AgentRunStore(root)
    crashing = controller_type(
        store,
        _scan_registry(required_approval_scope="approved_change"),
        ReleaseAgentWorkflowService(),
        None,
    )
    with pytest.raises(_InjectedCrash):
        crashing.run(
            LoopRequest(
                project_path=SAMPLE,
                task_kind="REVIEW",
                budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
            )
        )
    run_id = crashing.last_run_id
    store.close()

    reopened = AgentRunStore(root)
    resumed = LoopController(
        reopened,
        _scan_registry(required_approval_scope="approved_change"),
        ReleaseAgentWorkflowService(),
        None,
    ).resume(
        run_id,
        approval={
            "approval_id": "approval:future",
            "approved_scopes": ["approved_change"],
        },
    )

    assert resumed.status == "WAITING_HITL"
    assert resumed.state.pending_approval_id is not None
    assert not any(
        event.event_kind == "TOOL_STARTED"
        for event in reopened.events(run_id)
    )
    reopened.close()


def test_crash_resume_reconstructs_precrash_runtime_trace(tmp_path: Path) -> None:
    root = tmp_path / "crash-trace"
    store = AgentRunStore(root)
    crashing = _CrashAfterCompletedController(
        store,
        build_release_tool_registry(allowed_roots=(SAMPLE,)),
        ReleaseAgentWorkflowService(),
        ExecutionTracer(run_id="before-crash"),
    )
    with pytest.raises(_InjectedCrash):
        crashing.run(
            LoopRequest(
                project_path=SAMPLE,
                task_kind="REVIEW",
                budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
            )
        )
    run_id = crashing.last_run_id
    store.close()

    reopened = AgentRunStore(root)
    resumed = LoopController(
        reopened,
        build_release_tool_registry(allowed_roots=(SAMPLE,)),
        ReleaseAgentWorkflowService(),
        ExecutionTracer(run_id="after-crash"),
    ).resume(run_id)

    assert resumed.status == "COMPLETED"
    trace = json.loads(resumed.trace_path.read_text(encoding="utf-8"))
    runtime_kinds = [
        event["event_kind"]
        for event in trace["events"]
        if event["kind"] == "runtime_event"
    ]
    assert runtime_kinds[:5] == [
        "RUN_CREATED",
        "PLAN_PROPOSED",
        "TOOL_REQUESTED",
        "TOOL_STARTED",
        "TOOL_COMPLETED",
    ]
    assert any(
        event.get("run_id") == run_id
        and event.get("idempotency_key") == f"{run_id}:0:scan_project"
        and event.get("guardrail_decision") == "ALLOW"
        for event in trace["events"]
    )
    checkpoint = next(
        event
        for event in trace["events"]
        if event.get("event_kind") == "CHECKPOINT_COMMITTED"
    )
    assert checkpoint["checkpoint_sequence"] == checkpoint["event_sequence"]
    reopened.close()


def test_trace_reconstruction_is_stable_ordered_and_hitl_complete(
    tmp_path: Path,
) -> None:
    root = tmp_path / "stable-hitl-trace"
    store = AgentRunStore(root)
    initial = LoopController(
        store,
        _scan_registry(required_approval_scope="approved_change"),
        ReleaseAgentWorkflowService(),
        None,
    ).run(
        LoopRequest(
            project_path=SAMPLE,
            task_kind="REVIEW",
            budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
        )
    )
    approval_id = initial.state.pending_approval_id
    assert approval_id is not None
    store.close()

    first_store = AgentRunStore(root)
    first_tracer = ExecutionTracer(run_id="fresh-one")
    completed = LoopController(
        first_store,
        _scan_registry(required_approval_scope="approved_change"),
        ReleaseAgentWorkflowService(),
        first_tracer,
    ).resume(
        initial.run_id,
        approval=_approved_grant(first_store, initial.run_id),
    )
    assert completed.status == "COMPLETED"
    first_store.close()

    second_store = AgentRunStore(root)
    second_tracer = ExecutionTracer(run_id="fresh-two")
    LoopController(
        second_store,
        _scan_registry(required_approval_scope="approved_change"),
        ReleaseAgentWorkflowService(),
        second_tracer,
    ).resume(initial.run_id)

    first = _canonical_runtime_trace(first_tracer.to_dict()["events"])
    second = _canonical_runtime_trace(second_tracer.to_dict()["events"])
    assert first == second
    assert len({item[0] for item in first}) == len(first)
    requested_index = next(
        index for index, item in enumerate(first) if item[1] == "TOOL_REQUESTED"
    )
    require_index = next(
        index for index, item in enumerate(first)
        if item[2] == "REQUIRE_HITL"
    )
    accepted_index = next(
        index
        for index, item in enumerate(first)
        if item[1] == "APPROVAL_DECIDED"
    )
    allow_index = next(
        index for index, item in enumerate(first) if item[2] == "ALLOW"
    )
    started_index = next(
        index for index, item in enumerate(first) if item[1] == "TOOL_STARTED"
    )
    assert (
        requested_index
        < require_index
        < accepted_index
        < allow_index
        < started_index
    )
    first_events = first_tracer.to_dict()["events"]
    approval_timeline = [
        event
        for event in first_events
        if event.get("kind") in {"runtime_event", "guardrail"}
        and (
            event.get("event_kind") in {"APPROVAL_DECIDED", "TOOL_STARTED"}
            or event.get("guardrail_decision") in {"REQUIRE_HITL", "ALLOW"}
        )
    ]
    accepted_time = next(
        str(event["start"])
        for event in approval_timeline
        if event.get("event_kind") == "APPROVAL_DECIDED"
    )
    allow_time = next(
        str(event["start"])
        for event in approval_timeline
        if event.get("guardrail_decision") == "ALLOW"
    )
    assert allow_time == accepted_time
    assert [
        event.get("event_kind") or event.get("guardrail_decision")
        for event in sorted(
            approval_timeline,
            key=lambda item: str(item["ordering_key"]),
        )
    ] == ["REQUIRE_HITL", "APPROVAL_DECIDED", "ALLOW", "TOOL_STARTED"]
    assert len({event["ordering_key"] for event in approval_timeline}) == 4
    second_store.close()


@pytest.mark.parametrize(
    "tampered_time",
    ["2026-08-11T00:00:00Z", "2027-08-11T00:00:00Z"],
)
def test_acceptance_timestamp_only_tamper_fails_replay(
    tmp_path: Path,
    tampered_time: str,
) -> None:
    root = tmp_path / "acceptance-time-tamper"
    store = AgentRunStore(root)
    waiting = LoopController(
        store,
        _scan_registry(required_approval_scope="approved_change"),
        ReleaseAgentWorkflowService(),
        None,
    ).run(
        LoopRequest(
            project_path=SAMPLE,
            task_kind="REVIEW",
            budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
        )
    )
    approval_id = waiting.state.pending_approval_id
    assert approval_id is not None
    completed = LoopController(
        store,
        _scan_registry(required_approval_scope="approved_change"),
        ReleaseAgentWorkflowService(),
        None,
    ).resume(
        waiting.run_id,
        approval=_approved_grant(store, waiting.run_id),
    )
    assert completed.status == "COMPLETED"
    store.close()

    with sqlite3.connect(root / "agent_runs.sqlite3") as connection:
        connection.execute(
            """UPDATE run_events SET created_at_utc = ?
               WHERE run_id = ? AND event_kind = 'APPROVAL_DECIDED'""",
            (tampered_time, waiting.run_id),
        )
        connection.commit()

    tampered = AgentRunStore(root)
    with pytest.raises(CorruptRunError, match="payload or hash"):
        tampered.load_state(waiting.run_id)
    tampered.close()


def test_equal_timestamp_fresh_resumes_have_identical_explicit_trace_order(
    tmp_path: Path,
) -> None:
    root = tmp_path / "equal-time-trace"
    store = AgentRunStore(root)
    waiting = LoopController(
        store,
        _scan_registry(required_approval_scope="approved_change"),
        ReleaseAgentWorkflowService(),
        None,
    ).run(
        LoopRequest(
            project_path=SAMPLE,
            task_kind="REVIEW",
            budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
        )
    )
    approval_id = waiting.state.pending_approval_id
    assert approval_id is not None
    grant = _approved_grant(store, waiting.run_id)
    completed = LoopController(
        store,
        _scan_registry(required_approval_scope="approved_change"),
        ReleaseAgentWorkflowService(),
        None,
    ).resume(
        waiting.run_id,
        approval=grant,
    )
    assert completed.status == "COMPLETED"
    store.close()
    # Keep the synthetic equal-time replay inside the signed grant's validity
    # window.  The explicit ordering test must not create a temporally invalid
    # approval event merely to make several trace timestamps equal.
    equal_time = grant.decided_at_utc
    _rewrite_event_times_with_matching_envelope_hash(
        root,
        waiting.run_id,
        {"TOOL_REQUESTED", "APPROVAL_DECIDED", "TOOL_STARTED"},
        equal_time,
    )

    traces: list[list[tuple[str, str | None, str | None, str, str, str]]] = []
    for suffix in ("one", "two"):
        reopened = AgentRunStore(root)
        tracer = ExecutionTracer(run_id=f"equal-{suffix}")
        LoopController(
            reopened,
            _scan_registry(required_approval_scope="approved_change"),
            ReleaseAgentWorkflowService(),
            tracer,
        ).resume(waiting.run_id)
        traces.append(_canonical_runtime_trace(tracer.to_dict()["events"]))
        reopened.close()

    assert traces[0] == traces[1]
    approval_trace = [
        item
        for item in traces[0]
        if item[1] in {"APPROVAL_DECIDED", "TOOL_STARTED"}
        or item[2] in {"REQUIRE_HITL", "ALLOW"}
    ]
    assert [item[1] or item[2] for item in sorted(approval_trace, key=lambda item: item[5])] == [
        "REQUIRE_HITL",
        "APPROVAL_DECIDED",
        "ALLOW",
        "TOOL_STARTED",
    ]


def test_trace_duplicate_rejects_ordering_key_mismatch() -> None:
    tracer = ExecutionTracer(run_id="ordering-conflict")
    common = {
        "run_id": "ordering-conflict",
        "step_index": 0,
        "idempotency_key": "key-1",
        "decision": "ALLOW",
        "event_sequence": 2,
        "occurred_at": "2026-08-12T00:00:00Z",
    }
    tracer.guardrail(**common, ordering_event_sequence=3)

    with pytest.raises(ValueError, match="conflicting data"):
        tracer.guardrail(**common, ordering_event_sequence=4)


@pytest.mark.parametrize(
    "bad_expected",
    [
        ["RUN_COMPLETED", "RUN_CREATED"],
        ["RUN_CREATED", "RUN_CREATED", "RUN_COMPLETED"],
    ],
)
def test_runtime_expected_events_require_ordered_occurrences(
    tmp_path: Path,
    bad_expected: list[str],
) -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    payload["runtime_cases"][0]["expected_events"] = bad_expected
    dataset = tmp_path / f"bad-order-{uuid.uuid4().hex}.json"
    dataset.write_text(json.dumps(payload), encoding="utf-8")

    result = EvaluationRunner(PROJECT_ROOT).run(dataset)

    assert result.passed is False
    assert result.details["runtime"]["cases"][0]["matched"] is False


def test_runtime_trace_observations_have_replay_fields_and_redact_cost_data() -> None:
    tracer = ExecutionTracer(run_id="trace-run")
    with tracer.span(
        "tool",
        tool="scan_project",
        step_index=3,
        idempotency_key="idem-3",
        guardrail_decision="ALLOW",
        approval_id="approval-3",
        checkpoint_sequence=7,
        cost={"estimated_usd": 0.25, "api_key": "sk-SOURCESECRET12345"},
    ):
        pass

    event = tracer.to_dict()["events"][0]
    assert event["run_id"] == "trace-run"
    assert event["step_index"] == 3
    assert event["idempotency_key"] == "idem-3"
    assert event["guardrail_decision"] == "ALLOW"
    assert event["approval_id"] == "approval-3"
    assert event["checkpoint_sequence"] == 7
    assert event["latency_ms"] >= 0.0
    assert event["cost"] == {
        "estimated_usd": 0.25,
        "[REDACTED_KEY_1]": "[REDACTED]",
    }


class _InjectedCrash(RuntimeError):
    pass


class _CrashAfterCompletedController(LoopController):
    def _after_event(self, event: RunEvent) -> None:
        if event.event_kind == "TOOL_COMPLETED":
            raise _InjectedCrash


class _CrashAfterExecutionController(LoopController):
    def _after_tool_execution(self, call, result) -> None:  # type: ignore[no-untyped-def]
        raise _InjectedCrash


class _CrashAfterRunCreatedController(LoopController):
    def _after_event(self, event: RunEvent) -> None:
        if event.event_kind == "RUN_CREATED":
            raise _InjectedCrash


class _CrashAfterPlanController(LoopController):
    def _after_event(self, event: RunEvent) -> None:
        if event.event_kind == "PLAN_PROPOSED":
            raise _InjectedCrash


class _CrashAfterApprovalDecidedController(LoopController):
    def _after_event(self, event: RunEvent) -> None:
        if event.event_kind == "APPROVAL_DECIDED":
            raise _InjectedCrash


def _canonical_runtime_trace(
    events: list[dict[str, Any]],
) -> list[tuple[str, str | None, str | None, str, str, str]]:
    return [
        (
            str(event["event_id"]),
            event.get("event_kind"),
            event.get("guardrail_decision"),
            str(event["start"]),
            str(event["end"]),
            str(event["ordering_key"]),
        )
        for event in events
        if event["kind"] in {"runtime_event", "guardrail"}
    ]


def _rewrite_event_times_with_matching_envelope_hash(
    root: Path,
    run_id: str,
    event_kinds: set[str],
    created_at_utc: str,
) -> None:
    with sqlite3.connect(root / "agent_runs.sqlite3") as connection:
        rows = connection.execute(
            """SELECT event_id, run_id, sequence, event_kind, payload_json
               FROM run_events WHERE run_id = ?""",
            (run_id,),
        ).fetchall()
        for event_id, stored_run_id, sequence, event_kind, payload_json in rows:
            if event_kind not in event_kinds:
                continue
            payload = json.loads(payload_json)
            envelope_hash = sha256_json(
                {
                    "event_id": event_id,
                    "run_id": stored_run_id,
                    "sequence": sequence,
                    "event_kind": event_kind,
                    "payload": payload,
                    "created_at_utc": created_at_utc,
                }
            )
            connection.execute(
                """UPDATE run_events
                   SET created_at_utc = ?, payload_sha256 = ?
                   WHERE run_id = ? AND sequence = ?""",
                (created_at_utc, envelope_hash, run_id, sequence),
            )
        connection.commit()


def _scan_registry(
    *,
    fail: bool = False,
    fail_once: bool = False,
    required_approval_scope: str | None = None,
    calls: dict[str, int] | None = None,
) -> ToolRegistry:
    service = ReleaseReviewService()
    call_counter = calls if calls is not None else {"count": 0}

    def scan(
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        call_counter["count"] += 1
        if fail or (fail_once and call_counter["count"] == 1):
            raise ConnectionError("deterministic execution failure")
        review = service.review(
            project_path=Path(args["project_path"]),
            include_pytest_execution=args["include_pytest_execution"],
        )
        report = review.to_dict()
        review_ref = f"review:{sha256_json(report)}"
        context.references[review_ref] = review
        return {
            "review_ref": review_ref,
            "release_allowed": review.release_allowed,
            "report": report,
        }

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="scan_project",
            version="1",
            input_schema={
                "project_path": str,
                "include_pytest_execution": bool,
            },
            output_schema={
                "review_ref": str,
                "release_allowed": bool,
                "report": dict,
            },
            side_effect="read_only",
            allowed_roots=(PROJECT_ROOT,),
            network_policy="offline",
            timeout_ms=60_000,
            max_retries=1,
            budget_cost=1,
            required_approval_scope=required_approval_scope,
        ),
        scan,
    )
    return registry


def _approved_grant(store: AgentRunStore, run_id: str) -> ApprovalGrant:
    requested = next(
        event
        for event in reversed(store.events(run_id))
        if event.event_kind == "APPROVAL_REQUESTED"
    )
    raw_request = requested.payload["request"]
    if not isinstance(raw_request, dict):
        raw_request = dict(raw_request)
    request = ApprovalRequest.from_dict(raw_request)
    return ApprovalGrant.issue(
        request,
        actor="runtime-test-approver",
        decision="APPROVED",
        decided_at_utc=request.issued_at_utc,
        identity_evidence="offline-test-identity",
    )


def _insert_event_row(
    root: Path,
    run_id: str,
    sequence: int,
    payload: dict[str, object],
) -> None:
    event_id = f"approval-tamper-{sequence}"
    created_at_utc = "2026-08-12T00:00:00Z"
    with sqlite3.connect(root / "agent_runs.sqlite3") as connection:
        connection.execute(
            """INSERT INTO run_events(
                   run_id, sequence, event_id, event_kind, payload_json,
                   payload_sha256, created_at_utc
               ) VALUES (?, ?, ?, 'APPROVAL_DECIDED', ?, ?, ?)""",
            (
                run_id,
                sequence,
                event_id,
                canonical_json(payload),
                run_event_digest(
                    event_id=event_id,
                    run_id=run_id,
                    sequence=sequence,
                    event_kind="APPROVAL_DECIDED",
                    payload=payload,
                    created_at_utc=created_at_utc,
                ),
                created_at_utc,
            ),
        )
        connection.commit()
