import copy
import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from releaseguard_agent.agent_tools import (
    EvidenceSearchTool,
    FixPlanTool,
    ReleaseWorkflowTools,
    RiskAnalysisTool,
    ScanProjectTool,
    build_release_tool_registry,
)
from releaseguard_agent.observability import ExecutionTracer
from releaseguard_agent.rag import RuleRetrievalService, get_default_rule_index_path
from releaseguard_agent.runtime.loop import LoopController, LoopRequest
from releaseguard_agent.runtime.models import (
    AgentRunState,
    ApprovalGrant,
    ApprovalRequest,
    RunBudget,
    RunEvent,
    canonical_json,
    run_event_digest,
    sha256_json,
)
from releaseguard_agent.runtime.store import AgentRunStore, CorruptRunError
from releaseguard_agent.runtime.tools import ToolCall, ToolRegistry, ToolSpec
from releaseguard_agent.services import ReleaseReviewService
from releaseguard_agent.services.agent_workflow_service import (
    ReleaseAgentWorkflowService,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE = PROJECT_ROOT / "sample_projects" / "clean_python_project"
BLOCKING_SAMPLE = PROJECT_ROOT / "sample_projects" / "fastapi_bad_project"


class InjectedCrash(RuntimeError):
    pass


class CrashAfterToolRequestedController(LoopController):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.crash_armed = True

    def _after_event(self, event: RunEvent) -> None:
        if self.crash_armed and event.event_kind == "TOOL_REQUESTED":
            self.crash_armed = False
            raise InjectedCrash("crash after durable request")


class CrashAfterToolResultController(LoopController):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.crash_armed = True

    def _after_tool_result(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.crash_armed:
            self.crash_armed = False
            raise InjectedCrash("crash after durable tool result")


class CrashAfterToolExecutionController(LoopController):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.crash_armed = True

    def _after_tool_execution(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.crash_armed:
            self.crash_armed = False
            raise InjectedCrash("crash before durable tool result")


class CrashAfterToolCompletedController(LoopController):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.crash_armed = True

    def _after_event(self, event: RunEvent) -> None:
        if self.crash_armed and event.event_kind == "TOOL_COMPLETED":
            self.crash_armed = False
            raise InjectedCrash("crash after durable completion event")


class CrashAfterToolFailedController(LoopController):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.crash_armed = True

    def _after_event(self, event: RunEvent) -> None:
        if self.crash_armed and event.event_kind == "TOOL_FAILED":
            self.crash_armed = False
            raise InjectedCrash("crash after durable tool failure")


class CrashAfterRunCreatedController(LoopController):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.crash_armed = True

    def _after_event(self, event: RunEvent) -> None:
        if self.crash_armed and event.event_kind == "RUN_CREATED":
            self.crash_armed = False
            raise InjectedCrash("crash after run creation")


class CrashAfterBudgetEvaluationController(LoopController):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.crash_armed = True

    def _after_event(self, event: RunEvent) -> None:
        if (
            self.crash_armed
            and event.event_kind == "EVALUATION_RECORDED"
            and event.payload.get("terminal_status") == "FAILED"
            and str(event.payload.get("stop_reason", "")).endswith(
                "budget_exhausted"
            )
        ):
            self.crash_armed = False
            raise InjectedCrash("crash after terminal budget evaluation")


class CrashAfterFailedEvaluationController(LoopController):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.crash_armed = True

    def _after_event(self, event: RunEvent) -> None:
        if (
            self.crash_armed
            and event.event_kind == "EVALUATION_RECORDED"
            and event.payload.get("terminal_status") == "FAILED"
        ):
            self.crash_armed = False
            raise InjectedCrash("crash after failed evaluation")


class CrashAfterRecoveredController(LoopController):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.crash_armed = True

    def _after_event(self, event: RunEvent) -> None:
        if self.crash_armed and event.event_kind == "RUN_RECOVERED":
            self.crash_armed = False
            raise InjectedCrash("crash after recovery marker")


class CountingScanTool(ScanProjectTool):
    def __init__(self) -> None:
        super().__init__(ReleaseReviewService())
        self.calls = 0

    def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        return super().invoke(*args, **kwargs)


class FailingScanTool(ScanProjectTool):
    def __init__(self) -> None:
        super().__init__(ReleaseReviewService())
        self.calls = 0

    def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise TimeoutError("injected transient failure")


def _components(
    root: Path,
    scan: ScanProjectTool,
    controller_type: type[LoopController] = LoopController,
    *,
    allowed_roots: tuple[Path, ...] | None = None,
    evidence: EvidenceSearchTool | None = None,
    risk: RiskAnalysisTool | None = None,
    fix_plan: FixPlanTool | None = None,
) -> tuple[LoopController, AgentRunStore]:
    tools = ReleaseWorkflowTools(
        scan=scan,
        evidence=evidence or EvidenceSearchTool(
            RuleRetrievalService(get_default_rule_index_path())
        ),
        risk=risk or RiskAnalysisTool(),
        fix_plan=fix_plan or FixPlanTool(),
    )
    store = AgentRunStore(root)
    controller = controller_type(
        store,
        build_release_tool_registry(
            tools,
            allowed_roots=allowed_roots or (SAMPLE.parent,),
        ),
        ReleaseAgentWorkflowService(tools=tools),
        ExecutionTracer("task-4-resume-test"),
    )
    return controller, store


def _approval_components(
    root: Path,
    scan: ScanProjectTool,
    controller_type: type[LoopController] = LoopController,
) -> tuple[LoopController, AgentRunStore]:
    """Build a one-stage registry whose scan requires an explicit grant."""

    tools = ReleaseWorkflowTools(
        scan=scan,
        evidence=EvidenceSearchTool(RuleRetrievalService(get_default_rule_index_path())),
        risk=RiskAnalysisTool(),
        fix_plan=FixPlanTool(),
    )
    registry = ToolRegistry()

    def guarded_scan(
        args: dict[str, object],
        context: object,
    ) -> dict[str, object]:
        assert isinstance(context, object)
        review = scan.invoke(
            Path(str(args["project_path"])),
            include_pytest_execution=bool(args["include_pytest_execution"]),
        )
        report = review.to_dict()
        review_ref = f"review:{sha256_json(report)}"
        # The handler cannot be reached before approval in this regression.
        return {
            "review_ref": review_ref,
            "release_allowed": review.release_allowed,
            "report": report,
        }

    registry.register(
        ToolSpec(
            name="scan_project",
            version="1",
            input_schema={"project_path": str, "include_pytest_execution": bool},
            output_schema={"review_ref": str, "release_allowed": bool, "report": dict},
            side_effect="project_write",
            allowed_roots=(SAMPLE.parent,),
            network_policy="offline",
            timeout_ms=60_000,
            max_retries=0,
            budget_cost=1,
            required_approval_scope="project.write",
        ),
        guarded_scan,
    )
    store = AgentRunStore(root)
    controller = controller_type(
        store,
        registry,
        ReleaseAgentWorkflowService(tools=tools),
        ExecutionTracer("task-4-resume-approval-test"),
    )
    return controller, store


def test_resume_repairs_crash_before_approval_request_and_waits_for_hitl(
    tmp_path: Path,
) -> None:
    """A durable approval-gated request must never bypass a missing HITL event."""

    initial_scan = CountingScanTool()
    crashed, store = _approval_components(
        tmp_path / "approval-request-crash",
        initial_scan,
        CrashAfterToolRequestedController,
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )

    with pytest.raises(InjectedCrash):
        crashed.run(request)

    run_id = crashed.last_run_id
    assert store.load_state(run_id).pending_approval_id is not None
    assert not any(
        event.event_kind == "APPROVAL_REQUESTED" for event in store.events(run_id)
    )
    store.close()

    resumed_scan = CountingScanTool()
    resumed, reopened = _approval_components(
        tmp_path / "approval-request-crash",
        resumed_scan,
    )

    result = resumed.resume(run_id)

    assert result.status == "WAITING_HITL"
    assert result.metrics["stop_reason"] == "approval_required"
    assert resumed_scan.calls == 0
    assert [event.event_kind for event in reopened.events(run_id)].count(
        "APPROVAL_REQUESTED"
    ) == 1
    reopened.close()


def test_resume_pauses_when_a_previously_approved_grant_has_expired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted approval is not authority to start after its expiry."""

    from releaseguard_agent.runtime import loop as runtime_loop
    from releaseguard_agent.runtime import store as runtime_store

    created_at = "2026-08-11T00:00:10Z"
    monkeypatch.setattr(runtime_store, "_utc_now", lambda: created_at)
    scan = CountingScanTool()
    controller, store = _approval_components(tmp_path / "expired-approval", scan)
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )
    initial = AgentRunState.created(
        run_id="expired-approval-run",
        task_kind=request.task_kind,
        project_root=request.project_path,
        budget=request.budget,
    )
    store.create_run(
        initial,
        request={
            "project_path": str(request.project_path),
            "task_kind": request.task_kind,
            "force_ai_review": False,
            "baseline_review": None,
        },
    )
    store.append(initial.run_id, 0, "PLAN_PROPOSED", {"plan_digest": "plan"})
    call = ToolCall.create(
        tool_name="scan_project",
        tool_version="1",
        args={
            "project_path": str(request.project_path),
            "include_pytest_execution": False,
        },
        run_id=initial.run_id,
        step_index=0,
        idempotency_key=f"{initial.run_id}:0:scan_project",
    )
    approval_request = ApprovalRequest.issue(
        run_id=call.run_id,
        tool_name=call.tool_name,
        tool_version=call.tool_version,
        args_sha256=call.args_sha256,
        allowed_paths=(str(request.project_path),),
        scope="project.write",
        step_index=call.step_index,
        idempotency_key=call.idempotency_key,
        issued_at_utc="2026-08-11T00:00:00Z",
        expires_at_utc="2026-08-11T00:00:30Z",
        requester_identity="releaseguard.runtime",
        identity_evidence="runtime-identity",
    )
    store.append(
        initial.run_id,
        1,
        "TOOL_REQUESTED",
        {
            "tool_name": call.tool_name,
            "tool_version": call.tool_version,
            "canonical_args": call.canonical_args,
            "args_sha256": call.args_sha256,
            "step_index": call.step_index,
            "idempotency_key": call.idempotency_key,
            "requires_approval": True,
            "approval_id": approval_request.approval_id,
            "approval_scope": approval_request.scope,
            "approval_paths": list(approval_request.allowed_paths),
        },
    )
    store.append(
        initial.run_id,
        2,
        "APPROVAL_REQUESTED",
        {"request": approval_request.to_dict()},
    )
    grant = ApprovalGrant.issue(
        approval_request,
        actor="test-approver",
        decision="APPROVED",
        decided_at_utc="2026-08-11T00:00:20Z",
        identity_evidence="test-identity",
    )
    monkeypatch.setattr(
        runtime_store,
        "_utc_now",
        lambda: "2026-08-11T00:00:25Z",
    )
    store.append(
        initial.run_id,
        3,
        "APPROVAL_DECIDED",
        {"grant": grant.to_dict()},
    )
    monkeypatch.setattr(runtime_loop, "_utc_now", lambda: "2026-08-11T00:01:00Z")

    result = controller.resume(initial.run_id)

    assert result.status == "PAUSED"
    assert result.metrics["stop_reason"] == "approval_grant_expired"
    assert scan.calls == 0
    assert store.events(initial.run_id)[-1].event_kind == "RUN_PAUSED"
    assert store.events(initial.run_id)[-1].payload["reason"] == "approval_grant_expired"
    store.close()


def test_crash_after_tool_requested_resumes_without_duplicate_execution(
    tmp_path: Path,
) -> None:
    scan = CountingScanTool()
    controller, store = _components(
        tmp_path / "crashed",
        scan,
        CrashAfterToolRequestedController,
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    events_after_crash = store.events(run_id)
    assert events_after_crash[-1].event_kind == "TOOL_REQUESTED"
    assert store.load_state(run_id).pending_tool_idempotency_key
    store.close()

    resumed_scan = CountingScanTool()
    resumed_controller, resumed_store = _components(
        tmp_path / "crashed", resumed_scan
    )

    resumed = resumed_controller.resume(run_id)

    assert resumed.status == "COMPLETED"
    assert scan.calls == 0
    assert resumed_scan.calls == 1
    kinds = [event.event_kind for event in resumed_store.events(resumed.run_id)]
    assert kinds.count("TOOL_REQUESTED") == 1
    assert "RUN_RECOVERED" in kinds
    recovered = next(
        event for event in resumed_store.events(resumed.run_id)
        if event.event_kind == "RUN_RECOVERED"
    )
    assert recovered.payload["checkpoint_digest"]

    uninterrupted_scan = CountingScanTool()
    uninterrupted, uninterrupted_store = _components(
        tmp_path / "uninterrupted", uninterrupted_scan
    )
    expected = uninterrupted.run(request)
    assert resumed.state.decision_digest == expected.state.decision_digest
    assert resumed.review is not None and resumed.review.release_allowed is True
    assert uninterrupted_scan.calls == 1
    resumed_store.close()
    uninterrupted_store.close()


def test_durable_tool_result_prevents_duplicate_execution_after_restart(
    tmp_path: Path,
) -> None:
    scan = CountingScanTool()
    controller, store = _components(
        tmp_path / "result-ledger",
        scan,
        CrashAfterToolResultController,
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    assert scan.calls == 1
    assert store.events(run_id)[-1].event_kind == "TOOL_STARTED"
    store.close()

    resumed_scan = CountingScanTool()
    resumed_controller, resumed_store = _components(
        tmp_path / "result-ledger", resumed_scan
    )
    resumed = resumed_controller.resume(run_id)

    assert resumed.status == "COMPLETED"
    assert resumed_scan.calls == 0
    assert resumed.review is not None and resumed.review.release_allowed is True
    resumed_store.close()


def test_started_call_without_durable_result_pauses_after_restart(
    tmp_path: Path,
) -> None:
    scan = CountingScanTool()
    controller, store = _components(
        tmp_path / "uncertain-execution",
        scan,
        CrashAfterToolExecutionController,
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    assert scan.calls == 1
    assert store.events(run_id)[-1].event_kind == "TOOL_STARTED"
    store.close()

    resumed_scan = CountingScanTool()
    resumed, resumed_store = _components(
        tmp_path / "uncertain-execution", resumed_scan
    )
    result = resumed.resume(run_id)

    assert result.status == "PAUSED"
    assert result.metrics["stop_reason"] == "execution_ambiguous"
    assert resumed_scan.calls == 0
    paused = next(
        event
        for event in resumed_store.events(run_id)
        if event.event_kind == "RUN_PAUSED"
    )
    assert paused.payload["reason"] == "execution_attempt_unresolved"
    resumed_store.close()


def test_same_controller_resume_pauses_a_started_attempt_without_a_durable_result(
    tmp_path: Path,
) -> None:
    """A retained in-memory owner/result is not proof safe to execute on resume."""

    scan = CountingScanTool()
    controller, store = _components(
        tmp_path / "same-controller-uncertain-execution",
        scan,
        CrashAfterToolExecutionController,
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    assert scan.calls == 1
    assert store.load_tool_result(
        ToolCall.create(
            tool_name="scan_project",
            tool_version="1",
            args={
                "project_path": str(SAMPLE),
                "include_pytest_execution": False,
            },
            run_id=run_id,
            step_index=0,
            idempotency_key=f"{run_id}:0:scan_project",
        )
    ) is None

    resumed = controller.resume(run_id)

    assert resumed.status == "PAUSED"
    assert resumed.metrics["stop_reason"] == "execution_ambiguous"
    assert scan.calls == 1
    assert store.events(run_id)[-1].event_kind == "RUN_PAUSED"
    store.close()


def test_started_call_recovery_ignores_changed_preexecution_policy(
    tmp_path: Path,
) -> None:
    scan = CountingScanTool()
    controller, store = _components(
        tmp_path / "uncertain-policy-change",
        scan,
        CrashAfterToolExecutionController,
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    assert scan.calls == 1
    store.close()

    resumed_scan = CountingScanTool()
    resumed, resumed_store = _components(
        tmp_path / "uncertain-policy-change",
        resumed_scan,
        allowed_roots=(
            PROJECT_ROOT / "sample_projects" / "fastapi_bad_project",
        ),
    )
    result = resumed.resume(run_id)

    assert result.status == "PAUSED"
    assert result.metrics["stop_reason"] == "execution_ambiguous"
    assert resumed_scan.calls == 0
    paused = next(
        event
        for event in resumed_store.events(run_id)
        if event.event_kind == "RUN_PAUSED"
    )
    assert paused.payload["reason"] == "execution_attempt_unresolved"
    resumed_store.close()


def test_mislabeled_retryable_precrash_history_fails_closed_on_resume(
    tmp_path: Path,
) -> None:
    root = tmp_path / "malformed-uncertain"
    scan = CountingScanTool()
    controller, store = _components(
        root,
        scan,
        CrashAfterToolExecutionController,
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=2, max_tool_calls=2, max_retries=1),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    assert scan.calls == 1
    store.close()

    recovery_scan = CountingScanTool()
    recovery, recovery_store = _components(
        root, recovery_scan, CrashAfterRecoveredController
    )
    with pytest.raises(InjectedCrash):
        recovery.resume(run_id)
    recovered_state = recovery_store.load_state(run_id)
    assert recovered_state.status == "RECOVERING"
    assert recovered_state.tool_started is True
    recovery_store.close()

    payload = {
        "idempotency_key": recovered_state.pending_tool_idempotency_key,
        "retryable": True,
        "failure_phase": "execution",
    }
    malformed_sequence = recovered_state.last_event_sequence + 1
    malformed_time = "2026-08-12T00:00:00Z"
    malformed_state = replace(
        recovered_state,
        status="RUNNING",
        pending_tool_idempotency_key=None,
        tool_started=False,
        retry_count=recovered_state.retry_count + 1,
        last_event_sequence=malformed_sequence,
    )
    with sqlite3.connect(root / "agent_runs.sqlite3") as connection:
        connection.execute(
            """INSERT INTO run_events(
                   run_id, sequence, event_id, event_kind, payload_json,
                   payload_sha256, created_at_utc
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                malformed_sequence,
                "malformed-uncertain",
                "TOOL_FAILED",
                canonical_json(payload),
                run_event_digest(
                    event_id="malformed-uncertain",
                    run_id=run_id,
                    sequence=malformed_sequence,
                    event_kind="TOOL_FAILED",
                    payload=payload,
                    created_at_utc=malformed_time,
                ),
                malformed_time,
            ),
        )
        connection.execute(
            """UPDATE run_snapshots
               SET sequence = ?, state_json = ?, state_sha256 = ?
               WHERE run_id = ?""",
            (
                malformed_sequence,
                canonical_json(malformed_state.to_dict()),
                sha256_json(malformed_state.to_dict()),
                run_id,
            ),
        )
        connection.commit()

    fresh_scan = CountingScanTool()
    fresh, fresh_store = _components(root, fresh_scan)
    with pytest.raises(CorruptRunError, match="replay"):
        fresh.resume(run_id)
    assert scan.calls == 1
    assert recovery_scan.calls == 0
    assert fresh_scan.calls == 0
    fresh_store.close()


def test_completed_scan_resumes_evaluation_without_spending_another_call(
    tmp_path: Path,
) -> None:
    scan = CountingScanTool()
    controller, store = _components(
        tmp_path / "completed-scan",
        scan,
        CrashAfterToolCompletedController,
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    assert scan.calls == 1
    assert store.load_state(run_id).pending_tool_idempotency_key is None
    store.close()

    resumed_scan = CountingScanTool()
    resumed_controller, resumed_store = _components(
        tmp_path / "completed-scan", resumed_scan
    )
    resumed = resumed_controller.resume(run_id)

    assert resumed.status == "COMPLETED"
    assert resumed_scan.calls == 0
    assert resumed.review is not None and resumed.review.release_allowed is True
    resumed_store.close()


def test_failed_durable_result_reconciles_before_valid_retry(
    tmp_path: Path,
) -> None:
    root = tmp_path / "failed-result-reconciliation"
    failing_scan = FailingScanTool()
    controller, store = _components(
        root,
        failing_scan,
        CrashAfterToolResultController,
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=2, max_tool_calls=2, max_retries=1),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    assert failing_scan.calls == 1
    assert store.events(run_id)[-1].event_kind == "TOOL_STARTED"
    store.close()

    resumed_scan = CountingScanTool()
    resumed, resumed_store = _components(root, resumed_scan)
    recovered = resumed.resume(run_id)

    assert recovered.status == "RUNNING"
    assert resumed_scan.calls == 0
    kinds = [event.event_kind for event in resumed_store.events(run_id)]
    assert kinds.count("TOOL_RESULT_RECONCILED") == 1
    assert kinds.count("TOOL_FAILED") == 1
    assert kinds.index("TOOL_RESULT_RECONCILED") < kinds.index("TOOL_FAILED")
    result = resumed.resume(run_id)
    assert result.status == "COMPLETED"
    assert resumed_scan.calls == 1
    resumed_store.close()


def test_completed_event_hash_must_match_durable_tool_result(
    tmp_path: Path,
) -> None:
    root = tmp_path / "completed-hash-mismatch"
    scan = CountingScanTool()
    controller, store = _components(
        root,
        scan,
        CrashAfterToolCompletedController,
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    completed = store.events(run_id)[-1]
    assert completed.event_kind == "TOOL_COMPLETED"
    store.close()

    tampered_payload = dict(completed.payload)
    tampered_payload["output_sha256"] = "0" * 64
    with sqlite3.connect(root / "agent_runs.sqlite3") as connection:
        connection.execute(
            """UPDATE run_events
               SET payload_json = ?, payload_sha256 = ?
               WHERE run_id = ? AND sequence = ?""",
            (
                json.dumps(
                    tampered_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                run_event_digest(
                    event_id=completed.event_id,
                    run_id=run_id,
                    sequence=completed.sequence,
                    event_kind=completed.event_kind,
                    payload=tampered_payload,
                    created_at_utc=completed.created_at_utc,
                ),
                run_id,
                completed.sequence,
            ),
        )
        connection.commit()

    resumed_scan = CountingScanTool()
    resumed, resumed_store = _components(root, resumed_scan)
    with pytest.raises(CorruptRunError, match="output hash"):
        resumed.resume(run_id)
    assert resumed_scan.calls == 0
    assert resumed_store.events(run_id)[-1].event_kind == "TOOL_COMPLETED"
    resumed_store.close()


def test_restart_preserves_baseline_review_for_verifier_routing(
    tmp_path: Path,
) -> None:
    baseline = ReleaseReviewService().review(
        project_path=PROJECT_ROOT / "sample_projects" / "fastapi_bad_project",
        include_pytest_execution=False,
    )
    scan = CountingScanTool()
    controller, store = _components(
        tmp_path / "baseline", scan, CrashAfterToolRequestedController
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="VERIFICATION",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
        baseline_review=baseline,
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    store.close()
    resumed_scan = CountingScanTool()
    resumed_controller, resumed_store = _components(
        tmp_path / "baseline", resumed_scan
    )

    resumed = resumed_controller.resume(run_id)

    assert resumed.status == "COMPLETED"
    assert resumed.route_history == (
        "scan",
        "verifier_agent",
        "verification_complete",
    )
    resumed_store.close()


def test_terminal_restart_materializes_result_without_overwriting_trace(
    tmp_path: Path,
) -> None:
    scan = CountingScanTool()
    controller, store = _components(tmp_path / "terminal", scan)
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )
    completed = controller.run(request)
    assert completed.trace_path is not None
    trace_hash = hashlib.sha256(completed.trace_path.read_bytes()).hexdigest()
    event_count = len(store.events(completed.run_id))
    run_id = completed.run_id
    store.close()

    restarted_scan = CountingScanTool()
    restarted, restarted_store = _components(
        tmp_path / "terminal", restarted_scan
    )
    resumed = restarted.resume(run_id)

    assert resumed.status == "COMPLETED"
    assert resumed.review is not None and resumed.review.release_allowed is True
    assert resumed.route_history == ("scan", "finalize_clean")
    assert resumed.trace_path is not None
    assert hashlib.sha256(resumed.trace_path.read_bytes()).hexdigest() == trace_hash
    assert len(restarted_store.events(run_id)) == event_count
    assert restarted_scan.calls == 0
    restarted_store.close()


def test_terminal_blocking_role_results_replay_without_any_handler(
    tmp_path: Path,
) -> None:
    retrieval = RuleRetrievalService(get_default_rule_index_path())

    class CountingEvidence(EvidenceSearchTool):
        def __init__(self) -> None:
            super().__init__(retrieval)
            self.calls = 0

        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            return super().invoke(*args, **kwargs)

    class CountingRisk(RiskAnalysisTool):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            return super().invoke(*args, **kwargs)

    class CountingFix(FixPlanTool):
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            return super().invoke(*args, **kwargs)

    initial_scan = CountingScanTool()
    initial_evidence = CountingEvidence()
    initial_risk = CountingRisk()
    initial_fix = CountingFix()
    controller, store = _components(
        tmp_path / "terminal-role-replay",
        initial_scan,
        allowed_roots=(BLOCKING_SAMPLE,),
        evidence=initial_evidence,
        risk=initial_risk,
        fix_plan=initial_fix,
    )
    completed = controller.run(
        LoopRequest(
            project_path=BLOCKING_SAMPLE,
            task_kind="REVIEW",
            budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=0),
        )
    )
    assert completed.status == "COMPLETED"
    assert (
        initial_scan.calls,
        initial_evidence.calls,
        initial_risk.calls,
        initial_fix.calls,
    ) == (1, 1, 1, 1)
    event_count = len(store.events(completed.run_id))
    store.close()

    replay_scan = CountingScanTool()
    replay_evidence = CountingEvidence()
    replay_risk = CountingRisk()
    replay_fix = CountingFix()
    replay, replay_store = _components(
        tmp_path / "terminal-role-replay",
        replay_scan,
        allowed_roots=(BLOCKING_SAMPLE,),
        evidence=replay_evidence,
        risk=replay_risk,
        fix_plan=replay_fix,
    )
    materialized = replay.resume(completed.run_id)

    assert materialized.status == "COMPLETED"
    assert materialized.state.decision_digest == completed.state.decision_digest
    assert materialized.route_history == completed.route_history
    assert (
        replay_scan.calls,
        replay_evidence.calls,
        replay_risk.calls,
        replay_fix.calls,
    ) == (0, 0, 0, 0)
    assert len(replay_store.events(completed.run_id)) == event_count
    replay_store.close()


def test_restart_after_tool_failed_checkpoints_before_real_retry(
    tmp_path: Path,
) -> None:
    failing_scan = FailingScanTool()
    controller, store = _components(
        tmp_path / "failed-boundary",
        failing_scan,
        CrashAfterToolFailedController,
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=2, max_tool_calls=2, max_retries=1),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    assert failing_scan.calls == 1
    assert store.events(run_id)[-1].event_kind == "TOOL_FAILED"
    assert store.load_state(run_id).retry_count == 1
    store.close()

    resumed_scan = CountingScanTool()
    resumed, resumed_store = _components(
        tmp_path / "failed-boundary", resumed_scan
    )
    result = resumed.resume(run_id)

    assert result.status == "COMPLETED"
    assert resumed_scan.calls == 1
    kinds = [event.event_kind for event in resumed_store.events(run_id)]
    assert kinds.count("TOOL_FAILED") == 1
    assert kinds.count("TOOL_REQUESTED") == 2
    assert result.state.retry_count == 1
    resumed_store.close()


def test_restart_preserves_preexecution_guardrail_failure_identity(
    tmp_path: Path,
) -> None:
    scan = CountingScanTool()
    controller, store = _components(
        tmp_path / "guardrail-boundary",
        scan,
        CrashAfterToolFailedController,
    )
    request = LoopRequest(
        project_path=PROJECT_ROOT,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    assert scan.calls == 0
    failed = store.events(run_id)[-1]
    assert failed.event_kind == "TOOL_FAILED"
    assert failed.payload["error_type"] == "path_not_allowed"
    assert failed.payload["failure_phase"] == "guardrail_denied"
    kinds = [event.event_kind for event in store.events(run_id)]
    assert kinds[-2:] == ["TOOL_REQUESTED", "TOOL_FAILED"]
    assert "TOOL_STARTED" not in kinds
    store.close()

    resumed_scan = CountingScanTool()
    resumed, resumed_store = _components(
        tmp_path / "guardrail-boundary", resumed_scan
    )
    result = resumed.resume(run_id)

    assert result.status == "FAILED"
    assert result.metrics["stop_reason"] == "path_not_allowed"
    assert resumed_scan.calls == 0
    resumed_store.close()


def test_restart_after_run_created_recovers_plan_before_tool_request(
    tmp_path: Path,
) -> None:
    scan = CountingScanTool()
    controller, store = _components(
        tmp_path / "created-boundary", scan, CrashAfterRunCreatedController
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    assert [event.event_kind for event in store.events(run_id)] == ["RUN_CREATED"]
    store.close()

    resumed_scan = CountingScanTool()
    resumed, resumed_store = _components(
        tmp_path / "created-boundary", resumed_scan
    )
    result = resumed.resume(run_id)

    assert result.status == "COMPLETED"
    assert resumed_scan.calls == 1
    assert [event.event_kind for event in resumed_store.events(run_id)][:3] == [
        "RUN_CREATED",
        "PLAN_PROPOSED",
        "TOOL_REQUESTED",
    ]
    resumed_store.close()


def test_terminal_budget_evaluation_resumes_without_advancing_checkpoint(
    tmp_path: Path,
) -> None:
    scan = FailingScanTool()
    controller, store = _components(
        tmp_path / "budget-evaluation",
        scan,
        CrashAfterBudgetEvaluationController,
    )
    request = LoopRequest(
        project_path=SAMPLE,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=2, max_retries=1),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    state = store.load_state(run_id)
    assert state.step_index == 0
    assert state.budget.max_steps == 1
    assert store.events(run_id)[-1].event_kind == "EVALUATION_RECORDED"
    store.close()

    resumed_scan = CountingScanTool()
    resumed, resumed_store = _components(
        tmp_path / "budget-evaluation", resumed_scan
    )
    result = resumed.resume(run_id)

    assert result.status == "FAILED"
    assert result.metrics["stop_reason"] == "step_budget_exhausted"
    assert result.state.step_index == 1
    assert resumed_scan.calls == 0
    assert resumed_store.events(run_id)[-1].event_kind == "RUN_FAILED"
    resumed_store.close()


def test_nonbudget_failure_evaluation_resume_preserves_checkpoint(
    tmp_path: Path,
) -> None:
    scan = CountingScanTool()
    controller, store = _components(
        tmp_path / "nonbudget-evaluation",
        scan,
        CrashAfterFailedEvaluationController,
    )
    request = LoopRequest(
        project_path=PROJECT_ROOT,
        task_kind="REVIEW",
        budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )

    with pytest.raises(InjectedCrash):
        controller.run(request)

    run_id = controller.last_run_id
    assert store.events(run_id)[-1].payload["stop_reason"] == "path_not_allowed"
    assert store.load_state(run_id).step_index == 0
    store.close()

    resumed_scan = CountingScanTool()
    resumed, resumed_store = _components(
        tmp_path / "nonbudget-evaluation", resumed_scan
    )
    result = resumed.resume(run_id)

    assert result.status == "FAILED"
    assert result.metrics["stop_reason"] == "path_not_allowed"
    assert result.state.step_index == 1
    assert resumed_scan.calls == 0
    assert [event.event_kind for event in resumed_store.events(run_id)][-2:] == [
        "CHECKPOINT_COMMITTED",
        "RUN_FAILED",
    ]
    resumed_store.close()


def test_source_secret_is_redacted_from_every_durable_boundary(
    tmp_path: Path,
) -> None:
    secrets = (
        "sk-SOURCESECRET12345",
        "ghp_1234567890abcdefghijklmnopqrstuv",
        "github_pat_11AA0abcdefghijklmnopqrstuv_1234567890ABCDEFGHIJ",
        "AKIAIOSFODNN7EXAMPLE",
        (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        ),
        "postgresql://release:supersecret@db.internal:5432/app",
        (
            "-----BEGIN PRIVATE KEY-----\n"
            "c291cmNlLWRlcml2ZWQtcHJpdmF0ZS1rZXk=\n"
            "-----END PRIVATE KEY-----"
        ),
    )
    project = tmp_path / "secret-project"
    project.mkdir()
    (project / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (project / "source-secret.txt").write_text(
        "\n".join(secrets), encoding="utf-8"
    )

    class SourceSecretScanTool(ScanProjectTool):
        def invoke(self, project_path, **kwargs):  # type: ignore[no-untyped-def]
            review = super().invoke(project_path, **kwargs)
            payload = copy.deepcopy(review.report_payload)
            payload["source_observation"] = {
                "api_key": (Path(project_path) / "source-secret.txt").read_text(
                    encoding="utf-8"
                ),
                "note": "\n".join(secrets),
                **{secret: "benign" for secret in secrets},
            }
            return replace(review, report_payload=payload)

    root = tmp_path / "redacted-runtime"
    controller, store = _components(
        root,
        SourceSecretScanTool(ReleaseReviewService()),
        allowed_roots=(project,),
    )
    result = controller.run(
        LoopRequest(
            project_path=project,
            task_kind="REVIEW",
            budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=0),
        )
    )
    assert result.status == "COMPLETED"
    run_id = result.run_id
    store.close()

    restarted, restarted_store = _components(
        root,
        SourceSecretScanTool(ReleaseReviewService()),
        allowed_roots=(project,),
    )
    resumed = restarted.resume(run_id)
    assert resumed.status == "COMPLETED"
    restarted_store.close()

    with sqlite3.connect(root / "agent_runs.sqlite3") as connection:
        durable_rows = [
            *connection.execute("SELECT * FROM run_events").fetchall(),
            *connection.execute("SELECT * FROM run_snapshots").fetchall(),
            *connection.execute("SELECT * FROM tool_results").fetchall(),
        ]
    durable_text = repr(durable_rows)
    for secret in secrets:
        assert secret not in durable_text
    assert "[REDACTED]" in durable_text
