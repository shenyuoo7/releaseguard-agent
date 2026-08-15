from pathlib import Path
from typing import get_args

import pytest

from releaseguard_agent.runtime.models import (
    ApprovalGrant,
    ApprovalRequest,
    AgentRunState,
    AgentRunStatus,
    RunBudget,
    RunEvent,
    canonical_json,
    sha256_json,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_run_state_round_trips_and_canonical_json_is_stable() -> None:
    state = AgentRunState.created(
        run_id="run-1",
        task_kind="REVIEW",
        project_root=PROJECT_ROOT,
        budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=1),
    )

    assert AgentRunState.from_dict(state.to_dict()) == state
    assert canonical_json({"b": 1, "a": "✓"}) == '{"a":"✓","b":1}'
    assert sha256_json({"b": 1, "a": "✓"}) == sha256_json({"a": "✓", "b": 1})


def test_run_budget_rejects_negative_limits() -> None:
    with pytest.raises(ValueError, match="max_steps"):
        RunBudget(max_steps=0, max_tool_calls=1, max_retries=0)
    with pytest.raises(ValueError, match="max_tool_calls"):
        RunBudget(max_steps=1, max_tool_calls=-1, max_retries=0)
    with pytest.raises(ValueError, match="max_retries"):
        RunBudget(max_steps=1, max_tool_calls=1, max_retries=-1)


def test_run_state_rejects_invalid_constructor_values() -> None:
    with pytest.raises(ValueError, match="project_root"):
        AgentRunState(
            run_id="run-1",
            schema_version=1,
            task_kind="REVIEW",
            project_root=Path("relative"),
            status="CREATED",
            step_index=0,
            plan_digest=None,
            checkpoint_event_id=None,
            budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
            pending_approval_id=None,
        )


def test_run_state_rejects_missing_unknown_and_unsupported_serialized_values() -> None:
    state = AgentRunState.created(
        run_id="run-2",
        task_kind="REVIEW",
        project_root=PROJECT_ROOT,
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )
    invalid = state.to_dict()
    del invalid["run_id"]
    with pytest.raises(ValueError, match="missing"):
        AgentRunState.from_dict(invalid)

    invalid = state.to_dict()
    invalid["status"] = "UNKNOWN"
    with pytest.raises(ValueError, match="status"):
        AgentRunState.from_dict(invalid)

    invalid = state.to_dict()
    invalid["schema_version"] = 2
    with pytest.raises(ValueError, match="schema_version"):
        AgentRunState.from_dict(invalid)


def test_run_event_hash_is_stable_and_payload_is_copied() -> None:
    payload = {"nested": {"value": 1}}
    event = RunEvent.create(
        event_id="evt-1",
        run_id="run-1",
        sequence=0,
        event_kind="RUN_CREATED",
        payload=payload,
        created_at_utc="2026-08-11T00:00:00Z",
    )

    payload["nested"]["value"] = 2
    assert event.payload["nested"]["value"] == 1
    assert event.payload_sha256 == sha256_json(
        {
            "event_id": "evt-1",
            "run_id": "run-1",
            "sequence": 0,
            "event_kind": "RUN_CREATED",
            "payload": {"nested": {"value": 1}},
            "created_at_utc": "2026-08-11T00:00:00Z",
        }
    )
    assert event.to_dict()["payload_sha256"] == event.payload_sha256
    with pytest.raises(TypeError):
        event.payload["nested"] = {}
    with pytest.raises(TypeError):
        event.payload["nested"]["value"] = 3


def test_run_event_rejects_non_utc_or_malformed_timestamps() -> None:
    for timestamp in (
        "2026-08-11T08:00:00+08:00",
        "2026-08-11T00:00:00",
        "not-a-timestamp",
    ):
        with pytest.raises(ValueError, match="canonical UTC"):
            RunEvent.create(
                event_id="evt-invalid-time",
                run_id="run-1",
                sequence=0,
                event_kind="RUN_CREATED",
                payload={},
                created_at_utc=timestamp,
            )


def test_run_state_normalizes_mutable_memory_references_to_a_tuple() -> None:
    refs = ["memory-1"]
    state = AgentRunState(
        run_id="run-memory",
        schema_version=1,
        task_kind="REVIEW",
        project_root=PROJECT_ROOT,
        status="CREATED",
        step_index=0,
        plan_digest=None,
        checkpoint_event_id=None,
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
        pending_approval_id=None,
        memory_refs=refs,
    )

    refs.append("memory-2")
    assert state.memory_refs == ("memory-1",)


def test_status_is_a_closed_literal_protocol() -> None:
    assert set(get_args(AgentRunStatus)) == {
        "CREATED",
        "RUNNING",
        "WAITING_HITL",
        "PAUSED",
        "RECOVERING",
        "FAILED",
        "COMPLETED",
        "CANCELLED",
    }


def test_approval_request_and_grant_round_trip_with_exact_binding() -> None:
    request = ApprovalRequest.issue(
        run_id="run-approval",
        tool_name="apply_fix",
        tool_version="2",
        args_sha256="args-digest",
        allowed_paths=(str(PROJECT_ROOT / "pyproject.toml"),),
        scope="project.write",
        step_index=3,
        idempotency_key="operation-1",
        issued_at_utc="2026-08-11T00:00:00Z",
        expires_at_utc="2026-08-11T00:10:00Z",
        requester_identity="releaseguard.runtime",
        identity_evidence="local-runtime-identity",
    )
    grant = ApprovalGrant.issue(
        request,
        actor="reviewer@example.test",
        decision="APPROVED",
        decided_at_utc="2026-08-11T00:01:00Z",
        identity_evidence="signed-local-reviewer",
    )

    assert ApprovalRequest.from_dict(request.to_dict()) == request
    assert ApprovalGrant.from_dict(grant.to_dict()) == grant
    grant.verify_for(request, at_utc="2026-08-11T00:02:00Z")


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("run_id", "other-run"),
        ("tool_version", "3"),
        ("args_sha256", "changed-args"),
        ("allowed_paths", ["/changed/path"]),
        ("scope", "network.use"),
        ("step_index", 4),
        ("idempotency_key", "other-operation"),
        ("actor", "attacker@example.test"),
        ("expires_at_utc", "2026-08-11T00:20:00Z"),
    ],
)
def test_approval_grant_rejects_substitution(
    field: str,
    replacement: object,
) -> None:
    request = ApprovalRequest.issue(
        run_id="run-approval",
        tool_name="apply_fix",
        tool_version="2",
        args_sha256="args-digest",
        allowed_paths=(str(PROJECT_ROOT / "pyproject.toml"),),
        scope="project.write",
        step_index=3,
        idempotency_key="operation-1",
        issued_at_utc="2026-08-11T00:00:00Z",
        expires_at_utc="2026-08-11T00:10:00Z",
        requester_identity="releaseguard.runtime",
        identity_evidence="local-runtime-identity",
    )
    grant = ApprovalGrant.issue(
        request,
        actor="reviewer@example.test",
        decision="APPROVED",
        decided_at_utc="2026-08-11T00:01:00Z",
        identity_evidence="signed-local-reviewer",
    )
    tampered = grant.to_dict()
    tampered[field] = replacement

    with pytest.raises(ValueError, match="signature"):
        ApprovalGrant.from_dict(tampered)


def test_approval_grant_rejects_expiry_and_nonapproval_decisions() -> None:
    request = ApprovalRequest.issue(
        run_id="run-approval",
        tool_name="apply_fix",
        tool_version="2",
        args_sha256="args-digest",
        allowed_paths=(),
        scope="project.write",
        step_index=3,
        idempotency_key="operation-1",
        issued_at_utc="2026-08-11T00:00:00Z",
        expires_at_utc="2026-08-11T00:10:00Z",
        requester_identity="releaseguard.runtime",
        identity_evidence="local-runtime-identity",
    )
    expired = ApprovalGrant.issue(
        request,
        actor="reviewer@example.test",
        decision="APPROVED",
        decided_at_utc="2026-08-11T00:01:00Z",
        identity_evidence="signed-local-reviewer",
    )
    rejected = ApprovalGrant.issue(
        request,
        actor="reviewer@example.test",
        decision="REJECTED",
        decided_at_utc="2026-08-11T00:01:00Z",
        identity_evidence="signed-local-reviewer",
    )

    with pytest.raises(ValueError, match="expired"):
        expired.verify_for(request, at_utc="2026-08-11T00:10:01Z")
    with pytest.raises(ValueError, match="approved"):
        rejected.verify_for(request, at_utc="2026-08-11T00:02:00Z")
