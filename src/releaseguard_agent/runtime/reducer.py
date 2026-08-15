"""Strict, explicit reducer for the durable Agent event protocol."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import Any, Mapping

from .models import (
    AgentRunState,
    ApprovalGrant,
    ApprovalRequest,
    RunEvent,
    approval_id_for,
    canonical_json,
    sha256_json,
)


class InvalidTransitionError(ValueError):
    """Raised when replay sees an event that violates the run protocol."""


def validate_tool_requested_payload_pre_envelope(
    payload: Mapping[str, object],
) -> None:
    """Reject parsed credential arguments before an event envelope can exist.

    This lightweight ingress check intentionally does not replace replay
    validation below: malformed metadata remains a reducer concern, while a
    syntactically valid secret-bearing payload must never reach redaction,
    envelope hashing, or SQLite persistence.
    """

    canonical_args = payload.get("canonical_args")
    if not isinstance(canonical_args, str):
        return
    try:
        arguments = json.loads(canonical_args)
    except json.JSONDecodeError:
        return
    if not isinstance(arguments, dict):
        return
    from .tools import reject_sensitive_tool_arguments

    try:
        reject_sensitive_tool_arguments(arguments)
    except ValueError as exc:
        raise InvalidTransitionError(str(exc)) from exc


_TERMINAL = frozenset({"FAILED", "COMPLETED", "CANCELLED"})
_PAYLOAD_SCHEMAS = {
    "RUN_CREATED": ({"state"}, {"request"}),
    "PLAN_PROPOSED": (
        {"plan_digest"},
        {"force_ai_review", "baseline_review"},
    ),
    "TOOL_REQUESTED": (
        {"tool_name", "idempotency_key"},
        {
            "requires_approval",
            "tool_version",
            "canonical_args",
            "args_sha256",
            "step_index",
            "approval_id",
            "approval_scope",
            "approval_paths",
        },
    ),
    "APPROVAL_REQUESTED": ({"request"}, set()),
    "APPROVAL_DECIDED": ({"grant"}, set()),
    "TOOL_STARTED": (
        {"idempotency_key"},
        {"approval_id", "approval_grant_digest"},
    ),
    "TOOL_RESULT_RECONCILED": (
        {"idempotency_key", "result_digest"},
        set(),
    ),
    "TOOL_COMPLETED": ({"idempotency_key", "output_sha256"}, set()),
    "TOOL_FAILED": (
        {"idempotency_key"},
        {"retryable", "error_type", "failure_phase"},
    ),
    "EVALUATION_RECORDED": (
        {"decision_digest", "terminal_status"},
        {
            "review",
            "route_history",
            "manual_review_required",
            "stop_reason",
        },
    ),
    "CHECKPOINT_COMMITTED": ({"checkpoint_event_id", "step_index"}, set()),
    "RUN_PAUSED": ({"reason", "idempotency_key"}, set()),
    "RUN_RESUMED": ({"resolution", "idempotency_key"}, set()),
    "RUN_RECOVERED": ({"checkpoint_digest"}, set()),
    "RUN_COMPLETED": ({"decision_digest"}, set()),
    "RUN_FAILED": ({"decision_digest", "error_type"}, set()),
    "RUN_CANCELLED": ({"reason"}, set()),
}


def reduce_event(state: AgentRunState | None, event: RunEvent) -> AgentRunState:
    """Validate and apply exactly one event to an immutable run state."""

    if state is None:
        if event.event_kind != "RUN_CREATED" or event.sequence != 0:
            raise InvalidTransitionError("RUN_CREATED must be first at sequence 0")
        raw_state = _validated_payload(event.event_kind, event.payload).get("state")
        if not isinstance(raw_state, Mapping):
            raise InvalidTransitionError("RUN_CREATED requires a state object")
        raw_request = event.payload.get("request")
        if raw_request is not None and not isinstance(raw_request, Mapping):
            raise InvalidTransitionError("RUN_CREATED request must be an object")
        created = AgentRunState.from_dict(raw_state)
        if created.run_id != event.run_id or created.status != "CREATED":
            raise InvalidTransitionError("RUN_CREATED state does not match event")
        return replace(created, last_event_sequence=0)

    if event.run_id != state.run_id:
        raise InvalidTransitionError("event run_id does not match current state")
    if event.sequence != state.last_event_sequence + 1:
        raise InvalidTransitionError("event sequence is stale, duplicate, or out of order")
    if state.status in _TERMINAL:
        raise InvalidTransitionError("terminal run cannot accept more events")

    handlers = {
        "PLAN_PROPOSED": _plan_proposed,
        "TOOL_REQUESTED": _tool_requested,
        "APPROVAL_REQUESTED": _approval_requested,
        "TOOL_STARTED": _tool_started,
        "TOOL_RESULT_RECONCILED": _tool_result_reconciled,
        "TOOL_COMPLETED": _tool_completed,
        "TOOL_FAILED": _tool_failed,
        "EVALUATION_RECORDED": _evaluation_recorded,
        "CHECKPOINT_COMMITTED": _checkpoint_committed,
        "RUN_PAUSED": _run_paused,
        "RUN_RESUMED": _run_resumed,
        "RUN_RECOVERED": _run_recovered,
        "RUN_COMPLETED": _run_completed,
        "RUN_FAILED": _run_failed,
        "RUN_CANCELLED": _run_cancelled,
    }
    try:
        validated_payload = _validated_payload(event.event_kind, event.payload)
        if event.event_kind == "APPROVAL_DECIDED":
            next_state = _approval_decided(
                state,
                validated_payload,
                at_utc=event.created_at_utc,
            )
        else:
            next_state = handlers[event.event_kind](state, validated_payload)
    except KeyError as exc:
        raise InvalidTransitionError(f"unknown event kind: {event.event_kind}") from exc
    return replace(next_state, last_event_sequence=event.sequence)


def _plan_proposed(state: AgentRunState, payload: Mapping[str, Any]) -> AgentRunState:
    _status(state, {"CREATED"}, "PLAN_PROPOSED")
    force_ai_review = payload.get("force_ai_review", False)
    if not isinstance(force_ai_review, bool):
        raise InvalidTransitionError("force_ai_review must be boolean")
    baseline_review = payload.get("baseline_review")
    if baseline_review is not None and not isinstance(baseline_review, Mapping):
        raise InvalidTransitionError("baseline_review must be an object")
    _phase(state, {"CREATED"}, "PLAN_PROPOSED")
    return replace(
        state,
        status="RUNNING",
        plan_digest=_string(payload, "plan_digest"),
        lifecycle_phase="PLANNED",
        evaluation_outcome=None,
    )


def _tool_requested(state: AgentRunState, payload: Mapping[str, Any]) -> AgentRunState:
    _status(state, {"RUNNING"}, "TOOL_REQUESTED")
    _phase(state, {"PLANNED", "CHECKPOINTED"}, "TOOL_REQUESTED")
    if state.lifecycle_phase == "CHECKPOINTED" and state.evaluation_outcome not in {
        "CONTINUE",
        "RETRY",
    }:
        raise InvalidTransitionError(
            "TOOL_REQUESTED is invalid after a terminal checkpoint phase"
        )
    if state.pending_tool_idempotency_key is not None:
        raise InvalidTransitionError("another tool request is already pending")
    if state.tool_call_count >= state.budget.max_tool_calls:
        raise InvalidTransitionError("tool-call budget exceeded")
    key = _string(payload, "idempotency_key")
    tool_name = _string(payload, "tool_name")
    approval = payload.get("requires_approval", False)
    if not isinstance(approval, bool):
        raise InvalidTransitionError("requires_approval must be boolean")
    raw_approval_id = payload.get("approval_id")
    raw_approval_scope = payload.get("approval_scope")
    raw_approval_paths = payload.get("approval_paths", ())
    if not isinstance(raw_approval_paths, (list, tuple)) or not all(
        isinstance(path, str) and path for path in raw_approval_paths
    ):
        raise InvalidTransitionError("approval_paths must contain non-empty strings")
    durable_metadata = False
    tool_version: str | None
    args_sha256: str | None
    if approval:
        durable_metadata = {
            "tool_name",
            "tool_version",
            "args_sha256",
        }.issubset(payload)
        if not durable_metadata:
            raise InvalidTransitionError(
                "approval-gated request requires exact durable tool metadata"
            )
        if raw_approval_id is None or raw_approval_scope is None:
            raise InvalidTransitionError(
                "approval-gated request requires approval identity and scope"
            )
        approval_id = _string(payload, "approval_id")
        approval_scope = _string(payload, "approval_scope")
        tool_version = _string(payload, "tool_version")
        args_sha256 = _string(payload, "args_sha256")
        allowed_paths = tuple(sorted(set(raw_approval_paths)))
        expected_approval_id = approval_id_for(
            state.run_id,
            state.step_index,
            key,
            tool_name=tool_name,
            tool_version=tool_version,
            args_sha256=args_sha256,
            allowed_paths=allowed_paths,
            scope=approval_scope,
        )
        if approval_id != expected_approval_id:
            raise InvalidTransitionError(
                "approval_id does not match the requested tool binding"
            )
    else:
        if raw_approval_id is not None or raw_approval_scope is not None:
            raise InvalidTransitionError(
                "approval identity requires an approval-gated request"
            )
        approval_id = None
        approval_scope = None
        tool_version = payload.get("tool_version")
        args_sha256 = payload.get("args_sha256")
        allowed_paths = ()
    _validate_durable_tool_call(payload)
    requested_step = payload.get("step_index")
    if requested_step is not None and (
        isinstance(requested_step, bool)
        or not isinstance(requested_step, int)
        or requested_step != state.step_index
    ):
        raise InvalidTransitionError("requested step_index must match current state")
    return replace(
        state,
        status="RUNNING",
        pending_approval_id=approval_id,
        pending_approval_scope=approval_scope,
        pending_tool_name=tool_name,
        pending_tool_version=(
            tool_version if isinstance(tool_version, str) else None
        ),
        pending_tool_args_sha256=(
            args_sha256 if isinstance(args_sha256, str) else None
        ),
        pending_tool_allowed_paths=allowed_paths,
        pending_tool_idempotency_key=key,
        tool_started=False,
        tool_call_count=state.tool_call_count + 1,
        lifecycle_phase="TOOL_REQUESTED",
        evaluation_outcome=None,
    )


def _approval_requested(
    state: AgentRunState,
    payload: Mapping[str, Any],
) -> AgentRunState:
    _status(state, {"RUNNING"}, "APPROVAL_REQUESTED")
    _phase(state, {"TOOL_REQUESTED"}, "APPROVAL_REQUESTED")
    raw_request = payload.get("request")
    if not isinstance(raw_request, Mapping):
        raise InvalidTransitionError("APPROVAL_REQUESTED requires a request object")
    try:
        request = ApprovalRequest.from_dict(raw_request)
    except ValueError as exc:
        raise InvalidTransitionError("approval request is invalid") from exc
    if (
        request.run_id != state.run_id
        or request.step_index != state.step_index
        or request.idempotency_key != state.pending_tool_idempotency_key
        or request.approval_id != state.pending_approval_id
        or request.scope != state.pending_approval_scope
        or request.tool_name != state.pending_tool_name
        or request.tool_version != state.pending_tool_version
        or request.args_sha256 != state.pending_tool_args_sha256
        or request.allowed_paths != state.pending_tool_allowed_paths
    ):
        raise InvalidTransitionError("approval request does not match pending call")
    return replace(
        state,
        status="WAITING_HITL",
        lifecycle_phase="APPROVAL_PENDING",
        pending_approval_request_digest=sha256_json(request.to_dict()),
    )


def _approval_decided(
    state: AgentRunState,
    payload: Mapping[str, Any],
    *,
    at_utc: str,
) -> AgentRunState:
    _status(state, {"WAITING_HITL"}, "APPROVAL_DECIDED")
    _phase(state, {"APPROVAL_PENDING"}, "APPROVAL_DECIDED")
    raw_grant = payload.get("grant")
    if not isinstance(raw_grant, Mapping):
        raise InvalidTransitionError("APPROVAL_DECIDED requires a grant object")
    try:
        grant = ApprovalGrant.from_dict(raw_grant)
    except ValueError as exc:
        raise InvalidTransitionError("approval grant signature is invalid") from exc
    if (
        grant.run_id != state.run_id
        or grant.step_index != state.step_index
        or grant.idempotency_key != state.pending_tool_idempotency_key
        or grant.approval_id != state.pending_approval_id
        or grant.scope != state.pending_approval_scope
        or grant.tool_name != state.pending_tool_name
        or grant.tool_version != state.pending_tool_version
        or grant.args_sha256 != state.pending_tool_args_sha256
        or grant.allowed_paths != state.pending_tool_allowed_paths
    ):
        raise InvalidTransitionError("approval grant does not match pending call")
    if grant.decision != "APPROVED":
        raise InvalidTransitionError("approval grant is not approved")
    expires = datetime.fromisoformat(grant.expires_at_utc.replace("Z", "+00:00"))
    decided = datetime.fromisoformat(grant.decided_at_utc.replace("Z", "+00:00"))
    decision_time = datetime.fromisoformat(at_utc.replace("Z", "+00:00"))
    if decision_time < decided or decision_time > expires:
        raise InvalidTransitionError(
            "approval grant is not yet effective or is expired"
        )
    return replace(
        state,
        status="RECOVERING",
        lifecycle_phase="APPROVAL_GRANTED",
        approved_grant_digest=sha256_json(grant.to_dict()),
    )


def _tool_started(state: AgentRunState, payload: Mapping[str, Any]) -> AgentRunState:
    _status(state, {"RUNNING", "RECOVERING"}, "TOOL_STARTED")
    _matching_key(state, payload, "TOOL_STARTED")
    if state.tool_started:
        raise InvalidTransitionError("tool is already started")
    _phase(
        state,
        {"TOOL_REQUESTED", "APPROVAL_GRANTED", "RECOVERING"},
        "TOOL_STARTED",
    )
    raw_approval_id = payload.get("approval_id")
    raw_grant_digest = payload.get("approval_grant_digest")
    if state.pending_approval_id is not None:
        if state.status != "RECOVERING":
            raise InvalidTransitionError(
                "approval-gated TOOL_STARTED requires APPROVAL_DECIDED"
            )
        if state.approved_grant_digest is None:
            raise InvalidTransitionError(
                "approval-gated TOOL_STARTED requires a verified grant"
            )
        if raw_approval_id != state.pending_approval_id:
            raise InvalidTransitionError(
                "TOOL_STARTED approval_id does not match pending approval"
            )
        if raw_grant_digest != state.approved_grant_digest:
            raise InvalidTransitionError(
                "TOOL_STARTED approval grant digest does not match pending grant"
            )
    elif raw_approval_id is not None:
        raise InvalidTransitionError(
            "TOOL_STARTED approval_id has no pending approval"
        )
    return replace(
        state,
        status="RUNNING",
        tool_started=True,
        pending_approval_id=None,
        pending_approval_scope=None,
        pending_approval_request_digest=None,
        approved_grant_digest=None,
        pending_tool_name=None,
        pending_tool_version=None,
        pending_tool_args_sha256=None,
        pending_tool_allowed_paths=(),
        lifecycle_phase="TOOL_STARTED",
    )


def _tool_result_reconciled(
    state: AgentRunState,
    payload: Mapping[str, Any],
) -> AgentRunState:
    """Leave recovery only after an exact durable result is acknowledged."""

    _status(state, {"RECOVERING"}, "TOOL_RESULT_RECONCILED")
    _phase(state, {"RECOVERING"}, "TOOL_RESULT_RECONCILED")
    _matching_key(state, payload, "TOOL_RESULT_RECONCILED")
    if not state.tool_started:
        raise InvalidTransitionError(
            "tool result reconciliation requires a pre-recovery started tool"
        )
    _string(payload, "result_digest")
    return replace(state, status="RUNNING", lifecycle_phase="TOOL_STARTED")


def _tool_completed(state: AgentRunState, payload: Mapping[str, Any]) -> AgentRunState:
    _status(state, {"RUNNING", "RECOVERING"}, "TOOL_COMPLETED")
    _matching_key(state, payload, "TOOL_COMPLETED")
    if not state.tool_started:
        raise InvalidTransitionError("tool must be started before completion")
    _phase(state, {"TOOL_STARTED", "RECOVERING"}, "TOOL_COMPLETED")
    _string(payload, "output_sha256")
    return replace(
        state,
        status="RUNNING",
        pending_tool_idempotency_key=None,
        tool_started=False,
        pending_tool_name=None,
        pending_tool_version=None,
        pending_tool_args_sha256=None,
        pending_tool_allowed_paths=(),
        lifecycle_phase="TOOL_TERMINAL",
    )


def _tool_failed(state: AgentRunState, payload: Mapping[str, Any]) -> AgentRunState:
    """Record either a pre-handler denial or a started execution failure."""

    _status(state, {"RUNNING", "RECOVERING"}, "TOOL_FAILED")
    _phase(
        state,
        {"TOOL_REQUESTED", "TOOL_STARTED", "RECOVERING"},
        "TOOL_FAILED",
    )
    _matching_key(state, payload, "TOOL_FAILED")
    retryable = payload.get("retryable", False)
    if not isinstance(retryable, bool):
        raise InvalidTransitionError("retryable must be boolean")
    if "error_type" in payload:
        _string(payload, "error_type")
    failure_phase = payload.get("failure_phase")
    if failure_phase is not None and failure_phase not in {
        "guardrail_denied",
        "execution",
        "uncertain_execution",
    }:
        raise InvalidTransitionError("failure_phase is invalid")
    if (
        state.status == "RECOVERING"
        and state.tool_started
        and failure_phase != "uncertain_execution"
    ):
        raise InvalidTransitionError(
            "a pre-recovery started tool requires uncertain_execution failure"
        )
    if failure_phase == "guardrail_denied" and state.tool_started:
        raise InvalidTransitionError(
            "guardrail denial must be recorded before TOOL_STARTED"
        )
    if failure_phase in {"execution", "uncertain_execution"} and not state.tool_started:
        raise InvalidTransitionError(
            "execution failure requires a started tool"
        )
    if failure_phase == "uncertain_execution" and state.status != "RECOVERING":
        raise InvalidTransitionError(
            "uncertain execution is valid only during recovery"
        )
    if failure_phase == "uncertain_execution" and retryable:
        raise InvalidTransitionError(
            "uncertain execution is permanently nonretryable"
        )
    if retryable:
        if state.retry_count >= state.budget.max_retries:
            raise InvalidTransitionError("retry budget exceeded")
        return replace(
            state,
            status="RUNNING",
            pending_tool_idempotency_key=None,
            tool_started=False,
            pending_tool_name=None,
            pending_tool_version=None,
            pending_tool_args_sha256=None,
            pending_tool_allowed_paths=(),
            retry_count=state.retry_count + 1,
            lifecycle_phase="TOOL_TERMINAL",
        )
    return replace(
        state,
        status="RUNNING",
        pending_tool_idempotency_key=None,
        tool_started=False,
        pending_tool_name=None,
        pending_tool_version=None,
        pending_tool_args_sha256=None,
        pending_tool_allowed_paths=(),
        lifecycle_phase="TOOL_TERMINAL",
    )


def _evaluation_recorded(state: AgentRunState, payload: Mapping[str, Any]) -> AgentRunState:
    _status(state, {"RUNNING"}, "EVALUATION_RECORDED")
    if state.pending_tool_idempotency_key is not None:
        raise InvalidTransitionError("cannot evaluate with a pending tool")
    _phase(state, {"TOOL_TERMINAL"}, "EVALUATION_RECORDED")
    review = payload.get("review")
    if review is not None and not isinstance(review, Mapping):
        raise InvalidTransitionError("review must be an object")
    route_history = payload.get("route_history")
    if route_history is not None and (
        not isinstance(route_history, (list, tuple))
        or not all(isinstance(route, str) and route for route in route_history)
    ):
        raise InvalidTransitionError("route_history must contain non-empty strings")
    manual_review_required = payload.get("manual_review_required")
    if manual_review_required is not None and not isinstance(
        manual_review_required, bool
    ):
        raise InvalidTransitionError("manual_review_required must be boolean")
    if "stop_reason" in payload:
        _string(payload, "stop_reason")
    terminal_status = payload.get("terminal_status")
    if terminal_status not in {
        "CONTINUE",
        "COMPLETED",
        "FAILED",
        "RETRY",
        "PAUSED",
    }:
        raise InvalidTransitionError("terminal_status is required and invalid")
    return replace(
        state,
        decision_digest=_string(payload, "decision_digest"),
        lifecycle_phase="EVALUATED",
        evaluation_outcome=terminal_status,
    )


def _checkpoint_committed(state: AgentRunState, payload: Mapping[str, Any]) -> AgentRunState:
    _status(state, {"RUNNING"}, "CHECKPOINT_COMMITTED")
    _phase(state, {"EVALUATED"}, "CHECKPOINT_COMMITTED")
    step_index = payload.get("step_index")
    if isinstance(step_index, bool) or not isinstance(step_index, int):
        raise InvalidTransitionError("step_index must be an integer")
    if step_index != state.step_index + 1:
        raise InvalidTransitionError("checkpoint step_index must advance by exactly one")
    if step_index > state.budget.max_steps:
        raise InvalidTransitionError("step budget exceeded")
    return replace(
        state,
        checkpoint_event_id=_string(payload, "checkpoint_event_id"),
        step_index=step_index,
        lifecycle_phase="CHECKPOINTED",
    )


def _run_paused(state: AgentRunState, payload: Mapping[str, Any]) -> AgentRunState:
    _status(state, {"RUNNING", "RECOVERING"}, "RUN_PAUSED")
    _phase(state, {"APPROVAL_GRANTED", "TOOL_STARTED", "RECOVERING"}, "RUN_PAUSED")
    _matching_key(state, payload, "RUN_PAUSED")
    _string(payload, "reason")
    return replace(state, status="PAUSED", lifecycle_phase="PAUSED")


def _run_resumed(state: AgentRunState, payload: Mapping[str, Any]) -> AgentRunState:
    _status(state, {"PAUSED"}, "RUN_RESUMED")
    _phase(state, {"PAUSED"}, "RUN_RESUMED")
    _matching_key(state, payload, "RUN_RESUMED")
    _string(payload, "resolution")
    return replace(state, status="RECOVERING", lifecycle_phase="RECOVERING")


def _run_recovered(state: AgentRunState, payload: Mapping[str, Any]) -> AgentRunState:
    if state.status == "WAITING_HITL":
        raise InvalidTransitionError(
            "APPROVAL_DECIDED is the only recovery transition from WAITING_HITL"
        )
    _status(state, {"RUNNING"}, "RUN_RECOVERED")
    _phase(state, {"TOOL_REQUESTED", "TOOL_STARTED"}, "RUN_RECOVERED")
    if state.pending_tool_idempotency_key is None:
        raise InvalidTransitionError("RUN_RECOVERED requires a pending tool")
    _string(payload, "checkpoint_digest")
    return replace(state, status="RECOVERING", lifecycle_phase="RECOVERING")


def _run_completed(state: AgentRunState, payload: Mapping[str, Any]) -> AgentRunState:
    _status(state, {"RUNNING"}, "RUN_COMPLETED")
    _phase(state, {"CHECKPOINTED"}, "RUN_COMPLETED")
    if state.evaluation_outcome != "COMPLETED":
        raise InvalidTransitionError(
            "RUN_COMPLETED requires a completed evaluation checkpoint phase"
        )
    if state.pending_tool_idempotency_key is not None:
        raise InvalidTransitionError("cannot complete with a pending tool")
    digest = _string(payload, "decision_digest")
    if digest != state.decision_digest:
        raise InvalidTransitionError("RUN_COMPLETED decision digest does not match evaluation")
    return replace(state, status="COMPLETED", lifecycle_phase="TERMINAL")


def _run_failed(state: AgentRunState, payload: Mapping[str, Any]) -> AgentRunState:
    _status(state, {"RUNNING"}, "RUN_FAILED")
    _phase(state, {"CHECKPOINTED"}, "RUN_FAILED")
    if state.evaluation_outcome != "FAILED":
        raise InvalidTransitionError(
            "RUN_FAILED requires a failed evaluation checkpoint phase"
        )
    if state.pending_tool_idempotency_key is not None:
        raise InvalidTransitionError("cannot fail with a pending tool")
    digest = _string(payload, "decision_digest")
    if digest != state.decision_digest:
        raise InvalidTransitionError(
            "RUN_FAILED decision digest does not match evaluation"
        )
    _string(payload, "error_type")
    return replace(state, status="FAILED", lifecycle_phase="TERMINAL")


def _run_cancelled(state: AgentRunState, payload: Mapping[str, Any]) -> AgentRunState:
    _status(
        state,
        {"CREATED", "RUNNING", "WAITING_HITL", "PAUSED", "RECOVERING"},
        "RUN_CANCELLED",
    )
    _phase(
        state,
        {
            "CREATED",
            "PLANNED",
            "TOOL_REQUESTED",
            "APPROVAL_PENDING",
            "APPROVAL_GRANTED",
            "EVALUATED",
            "CHECKPOINTED",
            "RECOVERING",
        },
        "RUN_CANCELLED",
    )
    if state.tool_started:
        raise InvalidTransitionError(
            "RUN_CANCELLED requires started execution to be reconciled first"
        )
    _string(payload, "reason")
    return replace(
        state,
        status="CANCELLED",
        lifecycle_phase="TERMINAL",
        pending_approval_id=None,
        pending_approval_scope=None,
        pending_approval_request_digest=None,
        approved_grant_digest=None,
        pending_tool_idempotency_key=None,
        pending_tool_name=None,
        pending_tool_version=None,
        pending_tool_args_sha256=None,
        pending_tool_allowed_paths=(),
    )


def _status(state: AgentRunState, allowed: set[str], event_kind: str) -> None:
    if state.status not in allowed:
        raise InvalidTransitionError(f"{event_kind} is invalid from status {state.status}")


def _phase(state: AgentRunState, allowed: set[str], event_kind: str) -> None:
    if state.lifecycle_phase not in allowed:
        raise InvalidTransitionError(
            f"{event_kind} is invalid from lifecycle phase {state.lifecycle_phase}"
        )


def _string(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise InvalidTransitionError(f"{name} must be a non-empty string")
    return value


def _matching_key(state: AgentRunState, payload: Mapping[str, Any], event_kind: str) -> None:
    if _string(payload, "idempotency_key") != state.pending_tool_idempotency_key:
        raise InvalidTransitionError(f"{event_kind} idempotency key does not match request")


def _validate_durable_tool_call(payload: Mapping[str, Any]) -> None:
    fields = {"tool_version", "canonical_args", "args_sha256"}
    present = fields.intersection(payload)
    if not present:
        return
    if present != fields:
        raise InvalidTransitionError(
            "durable tool metadata must include version, arguments, and hash"
        )
    _string(payload, "tool_version")
    canonical_args = _string(payload, "canonical_args")
    args_sha256 = _string(payload, "args_sha256")
    try:
        arguments = json.loads(canonical_args)
    except json.JSONDecodeError as exc:
        raise InvalidTransitionError("canonical_args must contain JSON") from exc
    if not isinstance(arguments, dict) or canonical_json(arguments) != canonical_args:
        raise InvalidTransitionError("canonical_args must be a canonical JSON object")
    # ToolCall.create() is not the only ingress: direct store appends must
    # reject parsed secrets before accepting an argument digest or event.
    from .tools import reject_sensitive_tool_arguments

    try:
        reject_sensitive_tool_arguments(arguments)
    except ValueError as exc:
        raise InvalidTransitionError(str(exc)) from exc
    if sha256_json(arguments) != args_sha256:
        raise InvalidTransitionError("args_sha256 does not match canonical_args")


def _validated_payload(event_kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        required, optional = _PAYLOAD_SCHEMAS[event_kind]
    except KeyError as exc:
        raise InvalidTransitionError(f"unknown event kind: {event_kind}") from exc
    actual = set(payload)
    unknown = actual - required - optional
    missing = required - actual
    if unknown:
        raise InvalidTransitionError(f"unknown payload fields: {sorted(unknown)}")
    if missing:
        raise InvalidTransitionError(f"missing payload fields: {sorted(missing)}")
    return payload
