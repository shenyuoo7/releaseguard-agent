from pathlib import Path

import pytest

from releaseguard_agent.runtime.models import (
    AgentRunState,
    ApprovalGrant,
    ApprovalRequest,
    RunBudget,
    RunEvent,
    approval_id_for,
    sha256_json,
)
from releaseguard_agent.runtime.reducer import (
    InvalidTransitionError,
    _validated_payload,
    reduce_event,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_state() -> AgentRunState:
    return AgentRunState.created(
        run_id="run-1",
        task_kind="REVIEW",
        project_root=PROJECT_ROOT,
        budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=1),
    )


def event(sequence: int, kind: str, payload: dict[str, object]) -> RunEvent:
    return RunEvent.create(
        event_id=f"evt-{sequence}",
        run_id="run-1",
        sequence=sequence,
        event_kind=kind,
        payload=payload,
        created_at_utc=f"2026-08-11T00:00:{sequence:02d}Z",
    )


def created_state() -> AgentRunState:
    state = make_state()
    return reduce_event(
        None,
        event(0, "RUN_CREATED", {"state": state.to_dict()}),
    )


def exact_approval_request(
    state: AgentRunState,
    key: str,
) -> ApprovalRequest:
    return ApprovalRequest.issue(
        run_id=state.run_id,
        tool_name="apply_fix",
        tool_version="1",
        args_sha256=sha256_json({}),
        allowed_paths=(),
        scope="approved_change",
        step_index=state.step_index,
        idempotency_key=key,
        issued_at_utc="2026-08-11T00:00:02Z",
        expires_at_utc="2026-08-11T00:00:30Z",
        requester_identity="releaseguard.runtime",
        identity_evidence="runtime-identity",
    )


def exact_approval_payload(request: ApprovalRequest) -> dict[str, object]:
    return {
        "tool_name": request.tool_name,
        "tool_version": request.tool_version,
        "canonical_args": "{}",
        "args_sha256": request.args_sha256,
        "step_index": request.step_index,
        "idempotency_key": request.idempotency_key,
        "requires_approval": True,
        "approval_id": request.approval_id,
        "approval_scope": request.scope,
        "approval_paths": list(request.allowed_paths),
    }


def test_reducer_rejects_incomplete_approval_request_before_recovery_bypass() -> None:
    state = reduce_event(
        created_state(),
        event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}),
    )
    key = "key-approval"
    approval_id = approval_id_for(state.run_id, state.step_index, key)
    with pytest.raises(InvalidTransitionError, match="exact durable"):
        reduce_event(
            state,
            event(
                2,
                "TOOL_REQUESTED",
                {
                    "tool_name": "apply_fix",
                    "idempotency_key": key,
                    "requires_approval": True,
                    "approval_id": approval_id,
                    "approval_scope": "approved_change",
                },
            ),
        )


def test_reducer_rejects_a_self_signed_grant_for_a_different_tool_binding() -> None:
    state = reduce_event(
        created_state(), event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"})
    )
    key = "same-operation"
    tool_b_request = exact_approval_request(state, key)
    tool_a_payload = exact_approval_payload(tool_b_request)
    tool_a_payload["tool_name"] = "scan_project"

    with pytest.raises(InvalidTransitionError, match="approval_id"):
        reduce_event(state, event(2, "TOOL_REQUESTED", tool_a_payload))


def test_reducer_allows_only_approval_decision_to_leave_waiting_hitl() -> None:
    state = reduce_event(
        created_state(),
        event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}),
    )
    key = "key-waiting"
    request = exact_approval_request(state, key)
    state = reduce_event(
        state,
        event(2, "TOOL_REQUESTED", exact_approval_payload(request)),
    )
    state = reduce_event(
        state,
        event(3, "APPROVAL_REQUESTED", {"request": request.to_dict()}),
    )

    with pytest.raises(InvalidTransitionError, match="WAITING_HITL"):
        reduce_event(
            state,
            event(
                4,
                "TOOL_FAILED",
                {
                    "idempotency_key": key,
                    "failure_phase": "guardrail_denied",
                    "error_type": "guardrail_blocked",
                },
            ),
        )


def test_reducer_rejects_legacy_approval_acceptance_event() -> None:
    state = reduce_event(
        created_state(),
        event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}),
    )
    key = "key-legacy"
    request = exact_approval_request(state, key)
    state = reduce_event(
        state, event(2, "TOOL_REQUESTED", exact_approval_payload(request))
    )
    state = reduce_event(
        state,
        event(3, "APPROVAL_REQUESTED", {"request": request.to_dict()}),
    )

    with pytest.raises(InvalidTransitionError, match="unknown event kind"):
        reduce_event(
            state,
            event(
                4,
                "APPROVAL_ACCEPTED",
                {
                    "approval_id": request.approval_id,
                    "approval_scope": request.scope,
                    "idempotency_key": key,
                },
            ),
        )


def test_reducer_applies_happy_path_and_checkpoint() -> None:
    state = created_state()
    state = reduce_event(state, event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}))
    state = reduce_event(
        state,
        event(2, "TOOL_REQUESTED", {"tool_name": "scan_project", "idempotency_key": "key-1"}),
    )
    state = reduce_event(state, event(3, "TOOL_STARTED", {"idempotency_key": "key-1"}))
    state = reduce_event(
        state,
        event(
            4,
            "TOOL_COMPLETED",
            {"idempotency_key": "key-1", "output_sha256": "out-1"},
        ),
    )
    state = reduce_event(
        state,
        event(
            5,
            "EVALUATION_RECORDED",
            {"decision_digest": "decision-1", "terminal_status": "COMPLETED"},
        ),
    )
    state = reduce_event(
        state,
        event(6, "CHECKPOINT_COMMITTED", {"checkpoint_event_id": "evt-6", "step_index": 1}),
    )
    state = reduce_event(state, event(7, "RUN_COMPLETED", {"decision_digest": "decision-1"}))

    assert state.status == "COMPLETED"
    assert state.step_index == 1
    assert state.checkpoint_event_id == "evt-6"
    assert state.decision_digest == "decision-1"
    assert state.last_event_sequence == 7


def test_reducer_requires_matching_tool_idempotency_key() -> None:
    state = reduce_event(
        created_state(),
        event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}),
    )
    state = reduce_event(
        state,
        event(2, "TOOL_REQUESTED", {"tool_name": "scan_project", "idempotency_key": "key-1"}),
    )
    with pytest.raises(InvalidTransitionError, match="idempotency"):
        reduce_event(state, event(3, "TOOL_COMPLETED", {"idempotency_key": "wrong", "output_sha256": "x"}))


def test_reducer_requires_started_tool_and_rejects_pending_tool_evaluation() -> None:
    state = reduce_event(created_state(), event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}))
    state = reduce_event(
        state,
        event(2, "TOOL_REQUESTED", {"tool_name": "scan", "idempotency_key": "key-1"}),
    )
    with pytest.raises(InvalidTransitionError, match="started"):
        reduce_event(state, event(3, "TOOL_COMPLETED", {"idempotency_key": "key-1", "output_sha256": "out"}))
    with pytest.raises(InvalidTransitionError, match="pending"):
        reduce_event(
            state,
            event(
                3,
                "EVALUATION_RECORDED",
                {"decision_digest": "decision-1", "terminal_status": "CONTINUE"},
            ),
        )

    state = reduce_event(state, event(3, "TOOL_STARTED", {"idempotency_key": "key-1"}))
    with pytest.raises(InvalidTransitionError, match="already started"):
        reduce_event(state, event(4, "TOOL_STARTED", {"idempotency_key": "key-1"}))


def test_reducer_rejects_unknown_payload_fields_for_all_known_events() -> None:
    payloads = {
        "RUN_CREATED": {"state": {}},
        "PLAN_PROPOSED": {"plan_digest": "plan-1"},
        "TOOL_REQUESTED": {"tool_name": "scan", "idempotency_key": "key-1"},
        "TOOL_STARTED": {"idempotency_key": "key-1"},
        "TOOL_RESULT_RECONCILED": {
            "idempotency_key": "key-1",
            "result_digest": "result-1",
        },
        "TOOL_COMPLETED": {"idempotency_key": "key-1", "output_sha256": "out"},
        "TOOL_FAILED": {"idempotency_key": "key-1"},
        "EVALUATION_RECORDED": {
            "decision_digest": "decision-1",
            "terminal_status": "COMPLETED",
        },
        "CHECKPOINT_COMMITTED": {"checkpoint_event_id": "evt-1", "step_index": 1},
        "RUN_RECOVERED": {"checkpoint_digest": "checkpoint-1"},
        "RUN_COMPLETED": {"decision_digest": "decision-1"},
        "RUN_FAILED": {
            "decision_digest": "decision-1",
            "error_type": "budget_exhausted",
        },
    }

    for kind, payload in payloads.items():
        with pytest.raises(InvalidTransitionError, match="unknown payload"):
            _validated_payload(kind, {**payload, "extra": True})


def test_reducer_rejects_legacy_hitl_shortcut_without_request_and_grant() -> None:
    state = reduce_event(
        created_state(),
        event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}),
    )
    with pytest.raises(InvalidTransitionError, match="exact durable"):
        reduce_event(
            state,
            event(
                2,
                "TOOL_REQUESTED",
                {
                    "tool_name": "apply_fix",
                    "idempotency_key": "key-2",
                    "requires_approval": True,
                    "approval_id": "approval:legacy",
                    "approval_scope": "approved_change",
                },
            ),
        )


def test_reducer_rejects_stale_unknown_and_terminal_events() -> None:
    state = created_state()
    with pytest.raises(InvalidTransitionError, match="sequence"):
        reduce_event(state, event(0, "PLAN_PROPOSED", {"plan_digest": "plan-1"}))
    with pytest.raises(InvalidTransitionError, match="sequence"):
        reduce_event(state, event(2, "PLAN_PROPOSED", {"plan_digest": "plan-1"}))
    with pytest.raises(InvalidTransitionError, match="unknown event"):
        reduce_event(state, event(1, "NOT_A_REAL_EVENT", {}))

    state = reduce_event(state, event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}))
    with pytest.raises(InvalidTransitionError, match="sequence"):
        reduce_event(state, event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}))
    state = reduce_event(
        state,
        event(2, "TOOL_REQUESTED", {"tool_name": "scan", "idempotency_key": "key-1"}),
    )
    state = reduce_event(state, event(3, "TOOL_STARTED", {"idempotency_key": "key-1"}))
    state = reduce_event(
        state,
        event(4, "TOOL_COMPLETED", {"idempotency_key": "key-1", "output_sha256": "out"}),
    )
    state = reduce_event(
        state,
        event(
            5,
            "EVALUATION_RECORDED",
            {"decision_digest": "decision-1", "terminal_status": "COMPLETED"},
        ),
    )
    state = reduce_event(
        state,
        event(6, "CHECKPOINT_COMMITTED", {"checkpoint_event_id": "cp-1", "step_index": 1}),
    )
    state = reduce_event(state, event(7, "RUN_COMPLETED", {"decision_digest": "decision-1"}))
    with pytest.raises(InvalidTransitionError, match="terminal"):
        reduce_event(state, event(8, "PLAN_PROPOSED", {"plan_digest": "late"}))


def test_reducer_marks_an_incomplete_tool_recovery_until_completion() -> None:
    state = reduce_event(
        created_state(),
        event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}),
    )
    state = reduce_event(
        state,
        event(
            2,
            "TOOL_REQUESTED",
            {
                "tool_name": "scan_project",
                "idempotency_key": "key-1",
            },
        ),
    )

    state = reduce_event(
        state,
        event(3, "RUN_RECOVERED", {"checkpoint_digest": "checkpoint-0"}),
    )

    assert state.status == "RECOVERING"
    state = reduce_event(
        state,
        event(4, "TOOL_STARTED", {"idempotency_key": "key-1"}),
    )
    state = reduce_event(
        state,
        event(
            5,
            "TOOL_COMPLETED",
            {"idempotency_key": "key-1", "output_sha256": "out-1"},
        ),
    )
    assert state.status == "RUNNING"
    assert state.pending_tool_idempotency_key is None


def test_reducer_rejects_retryable_uncertain_execution() -> None:
    state = reduce_event(
        created_state(),
        event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}),
    )
    state = reduce_event(
        state,
        event(
            2,
            "TOOL_REQUESTED",
            {"tool_name": "scan_project", "idempotency_key": "key-1"},
        ),
    )
    state = reduce_event(
        state,
        event(3, "TOOL_STARTED", {"idempotency_key": "key-1"}),
    )
    state = reduce_event(
        state,
        event(4, "RUN_RECOVERED", {"checkpoint_digest": "checkpoint-0"}),
    )

    with pytest.raises(InvalidTransitionError, match="permanently nonretryable"):
        reduce_event(
            state,
            event(
                5,
                "TOOL_FAILED",
                {
                    "idempotency_key": "key-1",
                    "retryable": True,
                    "failure_phase": "uncertain_execution",
                },
            ),
        )


def test_reducer_rejects_mislabeled_failure_for_precrash_started_call() -> None:
    state = reduce_event(
        created_state(),
        event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}),
    )
    state = reduce_event(
        state,
        event(
            2,
            "TOOL_REQUESTED",
            {"tool_name": "scan_project", "idempotency_key": "key-1"},
        ),
    )
    state = reduce_event(
        state,
        event(3, "TOOL_STARTED", {"idempotency_key": "key-1"}),
    )
    state = reduce_event(
        state,
        event(4, "RUN_RECOVERED", {"checkpoint_digest": "checkpoint-0"}),
    )

    with pytest.raises(InvalidTransitionError, match="uncertain_execution"):
        reduce_event(
            state,
            event(
                5,
                "TOOL_FAILED",
                {
                    "idempotency_key": "key-1",
                    "retryable": True,
                    "failure_phase": "execution",
                },
            ),
        )


def test_reducer_allows_retry_after_safe_postrecovery_start() -> None:
    state = reduce_event(
        created_state(),
        event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}),
    )
    state = reduce_event(
        state,
        event(
            2,
            "TOOL_REQUESTED",
            {"tool_name": "scan_project", "idempotency_key": "key-1"},
        ),
    )
    state = reduce_event(
        state,
        event(3, "RUN_RECOVERED", {"checkpoint_digest": "checkpoint-0"}),
    )
    assert state.status == "RECOVERING"

    state = reduce_event(
        state,
        event(4, "TOOL_STARTED", {"idempotency_key": "key-1"}),
    )
    assert state.status == "RUNNING"

    state = reduce_event(
        state,
        event(
            5,
            "TOOL_FAILED",
            {
                "idempotency_key": "key-1",
                "retryable": True,
                "failure_phase": "execution",
            },
        ),
    )
    assert state.retry_count == 1
    assert state.pending_tool_idempotency_key is None


def test_reducer_records_a_budget_failure_as_an_immutable_terminal_state() -> None:
    state = reduce_event(
        created_state(),
        event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"}),
    )
    state = reduce_event(
        state,
        event(2, "TOOL_REQUESTED", {"tool_name": "scan", "idempotency_key": "key-1"}),
    )
    state = reduce_event(
        state,
        event(
            3,
            "TOOL_FAILED",
            {
                "idempotency_key": "key-1",
                "failure_phase": "guardrail_denied",
                "error_type": "step_budget_exhausted",
            },
        ),
    )
    state = reduce_event(
        state,
        event(
            4,
            "EVALUATION_RECORDED",
            {"decision_digest": "budget-1", "terminal_status": "FAILED"},
        ),
    )
    state = reduce_event(
        state,
        event(
            5,
            "CHECKPOINT_COMMITTED",
            {"checkpoint_event_id": "budget-checkpoint", "step_index": 1},
        ),
    )

    state = reduce_event(
        state,
        event(
            6,
            "RUN_FAILED",
            {"decision_digest": "budget-1", "error_type": "step_budget_exhausted"},
        ),
    )

    assert state.status == "FAILED"
    with pytest.raises(InvalidTransitionError, match="terminal"):
        reduce_event(state, event(7, "PLAN_PROPOSED", {"plan_digest": "late"}))


def test_reducer_rejects_evaluation_before_a_tool_terminal_event() -> None:
    state = reduce_event(
        created_state(), event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"})
    )

    with pytest.raises(InvalidTransitionError, match="phase"):
        reduce_event(
            state,
            event(
                2,
                "EVALUATION_RECORDED",
                {
                    "decision_digest": "decision-1",
                    "terminal_status": "COMPLETED",
                },
            ),
        )


def test_reducer_requires_evaluation_and_checkpoint_before_next_request() -> None:
    state = reduce_event(
        created_state(), event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"})
    )
    state = reduce_event(
        state,
        event(
            2,
            "TOOL_REQUESTED",
            {"tool_name": "scan_project", "idempotency_key": "key-1"},
        ),
    )
    state = reduce_event(
        state, event(3, "TOOL_STARTED", {"idempotency_key": "key-1"})
    )
    state = reduce_event(
        state,
        event(
            4,
            "TOOL_COMPLETED",
            {"idempotency_key": "key-1", "output_sha256": "out-1"},
        ),
    )

    with pytest.raises(InvalidTransitionError, match="phase"):
        reduce_event(
            state,
            event(
                5,
                "TOOL_REQUESTED",
                {"tool_name": "analyze_risk", "idempotency_key": "key-2"},
            ),
        )

    state = reduce_event(
        state,
        event(
            5,
            "EVALUATION_RECORDED",
            {"decision_digest": "continue-1", "terminal_status": "CONTINUE"},
        ),
    )
    with pytest.raises(InvalidTransitionError, match="phase"):
        reduce_event(
            state,
            event(
                6,
                "TOOL_REQUESTED",
                {"tool_name": "analyze_risk", "idempotency_key": "key-2"},
            ),
        )

    state = reduce_event(
        state,
        event(
            6,
            "CHECKPOINT_COMMITTED",
            {"checkpoint_event_id": "checkpoint-1", "step_index": 1},
        ),
    )
    state = reduce_event(
        state,
        event(
            7,
            "TOOL_REQUESTED",
            {"tool_name": "analyze_risk", "idempotency_key": "key-2"},
        ),
    )

    assert state.lifecycle_phase == "TOOL_REQUESTED"


def test_reducer_requires_checkpoint_before_terminal_completion() -> None:
    state = reduce_event(
        created_state(), event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"})
    )
    state = reduce_event(
        state,
        event(
            2,
            "TOOL_REQUESTED",
            {"tool_name": "scan_project", "idempotency_key": "key-1"},
        ),
    )
    state = reduce_event(
        state, event(3, "TOOL_STARTED", {"idempotency_key": "key-1"})
    )
    state = reduce_event(
        state,
        event(
            4,
            "TOOL_COMPLETED",
            {"idempotency_key": "key-1", "output_sha256": "out-1"},
        ),
    )
    state = reduce_event(
        state,
        event(
            5,
            "EVALUATION_RECORDED",
            {"decision_digest": "decision-1", "terminal_status": "COMPLETED"},
        ),
    )

    with pytest.raises(InvalidTransitionError, match="phase"):
        reduce_event(
            state,
            event(6, "RUN_COMPLETED", {"decision_digest": "decision-1"}),
        )


def test_reducer_closes_pause_resume_recovery_and_cancel_transitions() -> None:
    state = reduce_event(
        created_state(), event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"})
    )
    state = reduce_event(
        state,
        event(
            2,
            "TOOL_REQUESTED",
            {"tool_name": "scan_project", "idempotency_key": "key-1"},
        ),
    )
    state = reduce_event(
        state, event(3, "TOOL_STARTED", {"idempotency_key": "key-1"})
    )
    state = reduce_event(
        state,
        event(
            4,
            "RUN_PAUSED",
            {"reason": "execution_ambiguous", "idempotency_key": "key-1"},
        ),
    )
    assert state.status == "PAUSED"

    with pytest.raises(InvalidTransitionError, match="PAUSED"):
        reduce_event(
            state,
            event(
                5,
                "TOOL_REQUESTED",
                {"tool_name": "scan_project", "idempotency_key": "key-2"},
            ),
        )

    state = reduce_event(
        state,
        event(
            5,
            "RUN_RESUMED",
            {"resolution": "reconcile", "idempotency_key": "key-1"},
        ),
    )
    assert state.status == "RECOVERING"
    with pytest.raises(InvalidTransitionError, match="reconciled"):
        reduce_event(
            state,
            event(
                6,
                "RUN_CANCELLED",
                {"reason": "operator_cancelled"},
            ),
        )

    safe = reduce_event(
        created_state(), event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"})
    )
    safe = reduce_event(
        safe,
        event(2, "RUN_CANCELLED", {"reason": "operator_cancelled"}),
    )
    assert safe.status == "CANCELLED"


def test_reducer_consumes_one_exact_durable_approval_grant() -> None:
    state = reduce_event(
        created_state(), event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"})
    )
    request = ApprovalRequest.issue(
        run_id=state.run_id,
        tool_name="apply_fix",
        tool_version="2",
        args_sha256=sha256_json({"project_path": "safe"}),
        allowed_paths=(str(PROJECT_ROOT / "pyproject.toml"),),
        scope="project.write",
        step_index=state.step_index,
        idempotency_key="operation-1",
        issued_at_utc="2026-08-11T00:00:02Z",
        expires_at_utc="2026-08-11T00:00:30Z",
        requester_identity="releaseguard.runtime",
        identity_evidence="runtime-identity",
    )
    state = reduce_event(
        state,
        event(
            2,
            "TOOL_REQUESTED",
            {
                "tool_name": "apply_fix",
                "tool_version": "2",
                "canonical_args": '{"project_path":"safe"}',
                "args_sha256": sha256_json({"project_path": "safe"}),
                "step_index": 0,
                "idempotency_key": "operation-1",
                "requires_approval": True,
                "approval_id": request.approval_id,
                "approval_scope": request.scope,
                "approval_paths": list(request.allowed_paths),
            },
        ),
    )
    assert state.status == "RUNNING"
    state = reduce_event(
        state,
        event(3, "APPROVAL_REQUESTED", {"request": request.to_dict()}),
    )
    assert state.status == "WAITING_HITL"
    grant = ApprovalGrant.issue(
        request,
        actor="reviewer@example.test",
        decision="APPROVED",
        decided_at_utc="2026-08-11T00:00:03Z",
        identity_evidence="reviewer-identity",
    )
    state = reduce_event(
        state,
        event(4, "APPROVAL_DECIDED", {"grant": grant.to_dict()}),
    )
    assert state.status == "RECOVERING"
    state = reduce_event(
        state,
        event(
            5,
            "TOOL_STARTED",
            {
                "idempotency_key": "operation-1",
                "approval_id": grant.approval_id,
                "approval_grant_digest": sha256_json(grant.to_dict()),
            },
        ),
    )

    assert state.pending_approval_id is None
    assert state.pending_approval_request_digest is None
    assert state.approved_grant_digest is None
    assert state.lifecycle_phase == "TOOL_STARTED"


def test_reducer_rejects_expired_or_differently_bound_approval_grants() -> None:
    state = reduce_event(
        created_state(), event(1, "PLAN_PROPOSED", {"plan_digest": "plan-1"})
    )
    request = ApprovalRequest.issue(
        run_id=state.run_id,
        tool_name="apply_fix",
        tool_version="2",
        args_sha256=sha256_json({}),
        allowed_paths=(),
        scope="project.write",
        step_index=0,
        idempotency_key="operation-1",
        issued_at_utc="2026-08-11T00:00:01Z",
        expires_at_utc="2026-08-11T00:00:03Z",
        requester_identity="releaseguard.runtime",
        identity_evidence="runtime-identity",
    )
    state = reduce_event(
        state,
        event(
            2,
            "TOOL_REQUESTED",
            {
                "tool_name": "apply_fix",
                "tool_version": "2",
                "canonical_args": "{}",
                "args_sha256": sha256_json({}),
                "step_index": 0,
                "idempotency_key": "operation-1",
                "requires_approval": True,
                "approval_id": request.approval_id,
                "approval_scope": request.scope,
                "approval_paths": [],
            },
        ),
    )
    state = reduce_event(
        state,
        event(3, "APPROVAL_REQUESTED", {"request": request.to_dict()}),
    )
    expired = ApprovalGrant.issue(
        request,
        actor="reviewer@example.test",
        decision="APPROVED",
        decided_at_utc="2026-08-11T00:00:02Z",
        identity_evidence="reviewer-identity",
    )

    with pytest.raises(InvalidTransitionError, match="expired"):
        reduce_event(
            state,
            event(4, "APPROVAL_DECIDED", {"grant": expired.to_dict()}),
        )
