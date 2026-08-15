"""Bounded durable controller for read-only ReleaseGuard tool execution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Protocol

from releaseguard_agent.observability import ExecutionTracer
from releaseguard_agent.agents.role_agents import (
    EvidenceAgentOutput,
    FixPlannerAgentOutput,
    RiskAgentOutput,
)
from releaseguard_agent.models.check_result import CheckResult, CheckStatus, RiskLevel
from releaseguard_agent.models.retrieval_evidence import RetrievalEvidence
from releaseguard_agent.services.release_review_service import (
    ReleaseReviewArtifacts,
    ReleaseReviewResult,
)
from releaseguard_agent.workflows import ReleaseAgentWorkflowResult

from .guardrails import GuardrailDecision, GuardrailEngine, ToolExecutionContext
from .models import (
    AgentRunState,
    AgentRunStatus,
    ApprovalGrant,
    ApprovalRequest,
    RunBudget,
    RunEvent,
    sha256_json,
)
from .store import AgentRunStore, CorruptRunError
from .tools import ToolCall, ToolRegistry, ToolResult, ToolSpec


class LoopEvaluator(Protocol):
    """Deterministic graph adapter required by the durable controller."""

    def evaluate(
        self,
        *,
        review: ReleaseReviewResult,
        force_ai_review: bool = False,
        baseline_review: ReleaseReviewResult | None = None,
        tracer: ExecutionTracer | None = None,
        trace_output_dir: Path | None = None,
    ) -> ReleaseAgentWorkflowResult: ...

    def evaluate_committed(
        self,
        *,
        review: ReleaseReviewResult,
        evidence_output: EvidenceAgentOutput | None = None,
        risk_output: RiskAgentOutput | None = None,
        fix_plan_output: FixPlannerAgentOutput | None = None,
        force_ai_review: bool = False,
        baseline_review: ReleaseReviewResult | None = None,
        tracer: ExecutionTracer | None = None,
        trace_output_dir: Path | None = None,
    ) -> ReleaseAgentWorkflowResult: ...

    def is_retryable_failure(self, error_type: str | None) -> bool: ...


class _ExpiredApprovalGrantError(ValueError):
    """A valid durable grant has lost authority before recovery can start."""


@dataclass(frozen=True)
class LoopRequest:
    """Inputs for one bounded, read-only durable review run."""

    project_path: Path
    task_kind: str
    budget: RunBudget
    force_ai_review: bool = False
    baseline_review: ReleaseReviewResult | None = None

    def __post_init__(self) -> None:
        project_path = Path(self.project_path).expanduser().resolve()
        if not project_path.is_absolute():
            raise ValueError("project_path must be absolute")
        if not isinstance(self.task_kind, str) or not self.task_kind:
            raise ValueError("task_kind must be non-empty")
        if not isinstance(self.budget, RunBudget):
            raise ValueError("budget must be a RunBudget")
        if not isinstance(self.force_ai_review, bool):
            raise ValueError("force_ai_review must be boolean")
        object.__setattr__(self, "project_path", project_path)


@dataclass(frozen=True)
class AgentRunResult:
    """Materialized durable outcome plus graph observations."""

    run_id: str
    status: AgentRunStatus
    state: AgentRunState
    review: ReleaseReviewResult | None
    route_history: tuple[str, ...]
    metrics: Mapping[str, object] = field(default_factory=dict)
    trace_path: Path | None = None


class LoopController:
    """Commit a bounded tool loop and resume incomplete read-only calls."""

    def __init__(
        self,
        store: AgentRunStore,
        registry: ToolRegistry,
        evaluator: LoopEvaluator,
        tracer: ExecutionTracer | None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._evaluator = evaluator
        self._tracer = tracer
        self._guardrails = GuardrailEngine()
        self._contexts: dict[str, ToolExecutionContext] = {}
        self._requests: dict[str, LoopRequest] = {}
        self._results: dict[str, AgentRunResult] = {}
        self._owner_id = uuid.uuid4().hex
        self.last_run_id = ""

    def run(self, request: LoopRequest) -> AgentRunResult:
        """Create and execute one new bounded run."""

        run_id = uuid.uuid4().hex
        self.last_run_id = run_id
        self._requests[run_id] = request
        created = AgentRunState.created(
            run_id=run_id,
            task_kind=request.task_kind,
            project_root=request.project_path,
            budget=request.budget,
        )
        plan = _plan_document(request)
        event = self._store.create_run(created, request=plan)
        self._trace_event(event)
        self._after_event(event)
        state = self._propose_plan(
            self._store.load_state(run_id), request
        )
        return self._drive(state, request)

    def resume(
        self,
        run_id: str,
        approval: object | None = None,
        *,
        resolution: str | None = None,
    ) -> AgentRunResult:
        """Resume a durable incomplete request without mutating terminal runs."""

        self.last_run_id = run_id
        state = self._store.load_state(run_id)
        self._rebuild_trace(state)
        request = self._requests.get(run_id) or self._request_from_events(state)
        self._validate_completed_results(state, request)
        if state.status in {"COMPLETED", "FAILED", "CANCELLED"}:
            cached = self._results.get(run_id)
            return cached or self._terminal_result(state)
        if state.status == "PAUSED":
            if resolution != "reconcile":
                return self._result(state, stop_reason="execution_ambiguous")
            state = self._append(
                state,
                "RUN_RESUMED",
                {
                    "resolution": resolution,
                    "idempotency_key": state.pending_tool_idempotency_key or "",
                },
            )
        if state.status == "CREATED":
            state = self._propose_plan(state, request)
        if (
            state.status == "RUNNING"
            and state.pending_approval_id is not None
            and state.pending_approval_request_digest is None
        ):
            state = self._restore_missing_approval_request(state, request)
        context = self._context(state)
        accepted_grant: ApprovalGrant | None = None
        if (
            state.status == "WAITING_HITL"
            and state.pending_approval_id is not None
        ):
            approval_request = self._pending_approval_request(state)
            accepted_grant = _approval_grant(
                approval,
                expected_request=approval_request,
            )
        if accepted_grant is not None:
            state = self._append(
                state,
                "APPROVAL_DECIDED",
                {"grant": accepted_grant.to_dict()},
            )
            context.approved_scopes = frozenset({accepted_grant.scope})
        elif (
            state.status == "RECOVERING"
            and state.pending_approval_id is not None
        ):
            try:
                accepted_grant = self._accepted_approval_grant(state)
            except _ExpiredApprovalGrantError:
                call = self._pending_call(state, request)
                return self._pause_ambiguous(
                    state,
                    call,
                    "approval_grant_expired",
                    stop_reason="approval_grant_expired",
                )
            context.approved_scopes = frozenset({accepted_grant.scope})
        if state.status == "WAITING_HITL" and accepted_grant is None:
            return self._result(state, stop_reason="approval_required")
        if state.pending_tool_idempotency_key is not None:
            if state.status != "RECOVERING":
                state = self._append(
                    state,
                    "RUN_RECOVERED",
                    {"checkpoint_digest": _checkpoint_digest(state)},
                )
            call = self._pending_call(state, request)
            return self._execute_pending(state, request, call, context)
        return self._resume_without_pending(state, request, context)

    def _restore_missing_approval_request(
        self,
        state: AgentRunState,
        request: LoopRequest,
    ) -> AgentRunState:
        """Complete the durable HITL boundary after a request-event crash."""

        call = self._pending_call(state, request)
        spec = self._registry.get(call.tool_name, call.tool_version)
        if spec is None:
            raise CorruptRunError("pending approval references an unknown tool")
        approval_request = _approval_request(call, spec)
        if (
            approval_request.approval_id != state.pending_approval_id
            or approval_request.scope != state.pending_approval_scope
            or approval_request.tool_name != state.pending_tool_name
            or approval_request.tool_version != state.pending_tool_version
            or approval_request.args_sha256 != state.pending_tool_args_sha256
            or approval_request.allowed_paths != state.pending_tool_allowed_paths
        ):
            raise CorruptRunError("pending approval request binding does not match")
        return self._append(
            state,
            "APPROVAL_REQUESTED",
            {"request": approval_request.to_dict()},
        )

    def _accepted_approval_grant(
        self,
        state: AgentRunState,
    ) -> ApprovalGrant:
        decided = next(
            (
                event
                for event in reversed(self._store.events(state.run_id))
                if event.event_kind == "APPROVAL_DECIDED"
            ),
            None,
        )
        raw_grant = decided.payload.get("grant") if decided is not None else None
        if not isinstance(raw_grant, Mapping):
            raise CorruptRunError(
                "recovering approval grant is missing"
            )
        try:
            grant = ApprovalGrant.from_dict(raw_grant)
        except ValueError as exc:
            raise CorruptRunError("recovering approval grant is invalid") from exc
        if sha256_json(grant.to_dict()) != state.approved_grant_digest:
            raise CorruptRunError("recovering approval grant digest does not match")
        request = self._pending_approval_request(state)
        try:
            grant.verify_for(request, at_utc=_utc_now())
        except ValueError as exc:
            if str(exc) == "approval grant is expired":
                raise _ExpiredApprovalGrantError(str(exc)) from exc
            raise CorruptRunError(
                "recovering approval grant no longer matches its request"
            ) from exc
        return grant

    def _pending_approval_request(self, state: AgentRunState) -> ApprovalRequest:
        requested = next(
            (
                event
                for event in reversed(self._store.events(state.run_id))
                if event.event_kind == "APPROVAL_REQUESTED"
            ),
            None,
        )
        raw_request = requested.payload.get("request") if requested is not None else None
        if not isinstance(raw_request, Mapping):
            raise CorruptRunError("pending approval request is missing")
        try:
            approval_request = ApprovalRequest.from_dict(raw_request)
        except ValueError as exc:
            raise CorruptRunError("pending approval request is invalid") from exc
        if sha256_json(approval_request.to_dict()) != state.pending_approval_request_digest:
            raise CorruptRunError("pending approval request digest does not match")
        return approval_request

    def _drive(
        self,
        state: AgentRunState,
        request: LoopRequest,
    ) -> AgentRunResult:
        context = self._context(state)
        while True:
            budget_error = _budget_error(state)
            if budget_error is not None:
                return self._fail_budget(state, budget_error)
            self._restore_committed_references(state, request, context)
            call = self._next_workflow_call(state, request)
            spec = self._registry.get(call.tool_name, call.tool_version)
            guardrail_decision = (
                self._guardrails.check(call, spec, context)
                if spec is not None
                else GuardrailDecision.BLOCK
            )
            requires_approval = guardrail_decision is GuardrailDecision.REQUIRE_HITL
            approval_request = (
                _approval_request(call, spec)
                if requires_approval and spec is not None
                else None
            )
            requested_payload: dict[str, object] = {
                "tool_name": call.tool_name,
                "tool_version": call.tool_version,
                "canonical_args": call.canonical_args,
                "args_sha256": call.args_sha256,
                "step_index": call.step_index,
                "idempotency_key": call.idempotency_key,
                "requires_approval": requires_approval,
            }
            if approval_request is not None:
                requested_payload["approval_id"] = approval_request.approval_id
                requested_payload["approval_scope"] = approval_request.scope
                requested_payload["approval_paths"] = list(
                    approval_request.allowed_paths
                )
            state = self._append(
                state,
                "TOOL_REQUESTED",
                requested_payload,
            )
            if approval_request is not None:
                state = self._append(
                    state,
                    "APPROVAL_REQUESTED",
                    {"request": approval_request.to_dict()},
                )
            result = self._execute_pending(state, request, call, context)
            if result.status != "RUNNING":
                return result
            state = result.state

    def _propose_plan(
        self,
        state: AgentRunState,
        request: LoopRequest,
    ) -> AgentRunState:
        plan = _plan_document(request)
        return self._append(
            state,
            "PLAN_PROPOSED",
            {
                "plan_digest": sha256_json(plan),
                "force_ai_review": request.force_ai_review,
                "baseline_review": plan["baseline_review"],
            },
        )

    def _execute_pending(
        self,
        state: AgentRunState,
        request: LoopRequest,
        call: ToolCall,
        context: ToolExecutionContext,
    ) -> AgentRunResult:
        tool_result = self._store.load_tool_result(call)
        if (
            tool_result is not None
            and state.status == "RECOVERING"
            and state.tool_started
        ):
            tool_result = self._store.reconcile_completed_tool_attempt(call)
            state = self._append(
                state,
                "TOOL_RESULT_RECONCILED",
                {
                    "idempotency_key": call.idempotency_key,
                    "result_digest": _tool_result_digest(tool_result),
                },
            )
        if not state.tool_started:
            spec = self._registry.get(call.tool_name, call.tool_version)
            if spec is None:
                return self._record_nonretryable_failure(
                    state, call, "unknown_tool"
                )
            context.retry_count = state.retry_count
            decision = self._guardrails.check(call, spec, context)
            if self._tracer is not None:
                run_events = self._store.events(state.run_id)
                requested = next(
                    event
                    for event in reversed(run_events)
                    if event.event_kind == "TOOL_REQUESTED"
                    and event.payload.get("idempotency_key")
                    == call.idempotency_key
                )
                occurred_at = requested.created_at_utc
                ordering_event_sequence = requested.sequence
                if decision is GuardrailDecision.ALLOW and (
                    state.pending_approval_id is not None
                ):
                    accepted = next(
                        event
                        for event in reversed(run_events)
                        if event.event_kind == "APPROVAL_DECIDED"
                        and isinstance(event.payload.get("grant"), Mapping)
                        and event.payload["grant"].get("idempotency_key")
                        == call.idempotency_key
                    )
                    occurred_at = accepted.created_at_utc
                    ordering_event_sequence = accepted.sequence
                self._tracer.guardrail(
                    run_id=state.run_id,
                    step_index=call.step_index,
                    idempotency_key=call.idempotency_key,
                    decision=decision.value,
                    approval_id=state.pending_approval_id,
                    event_sequence=requested.sequence,
                    ordering_event_sequence=ordering_event_sequence,
                    occurred_at=occurred_at,
                )
            if decision is GuardrailDecision.REQUIRE_HITL:
                return self._result(state, stop_reason="approval_required")
            if decision is not GuardrailDecision.ALLOW:
                error_type = self._guardrails.error_type(call, spec, context)
                return self._record_nonretryable_failure(
                    state,
                    call,
                    error_type or "guardrail_blocked",
                )
            started_payload: dict[str, object] = {
                "idempotency_key": call.idempotency_key,
            }
            if state.pending_approval_id is not None:
                started_payload["approval_id"] = state.pending_approval_id
            if state.approved_grant_digest is not None:
                started_payload["approval_grant_digest"] = (
                    state.approved_grant_digest
                )
            state = self._append(
                state,
                "TOOL_STARTED",
                started_payload,
            )
        attempt = self._store.load_tool_attempt(call)
        if (
            state.status == "RECOVERING"
            and state.tool_started
            and tool_result is None
        ):
            context.approved_scopes = frozenset()
            return self._pause_ambiguous(
                state,
                call,
                "execution_attempt_unresolved",
            )
        if attempt is None:
            attempt = self._store.start_tool_attempt(
                call,
                owner_id=self._owner_id,
            )
        if attempt.status == "AMBIGUOUS" or (
            attempt.status == "STARTED" and attempt.owner_id != self._owner_id
        ):
            context.approved_scopes = frozenset()
            return self._pause_ambiguous(state, call, "execution_attempt_unresolved")
        if tool_result is None:
            if self._tracer is None:
                tool_result = self._registry.execute(call, context)
            else:
                with self._tracer.span(
                    "tool",
                    tool=call.tool_name,
                    run_id=state.run_id,
                    step_index=call.step_index,
                    idempotency_key=call.idempotency_key,
                    guardrail_decision=GuardrailDecision.ALLOW.value,
                    approval_id=state.pending_approval_id,
                ) as span:
                    tool_result = self._registry.execute(call, context)
                    span.update(
                        tool_status=tool_result.status,
                        error_type=tool_result.error_type,
                    )
            context.approved_scopes = frozenset()
            self._after_tool_execution(call, tool_result)
            if (
                tool_result.status == "quarantined"
                and tool_result.error_type == "execution_timeout_ambiguous"
            ):
                self._store.mark_tool_attempt_ambiguous(
                    call,
                    reason="execution_timeout_ambiguous",
                )
                return self._pause_ambiguous(
                    state,
                    call,
                    "execution_timeout_ambiguous",
                )
            tool_result = self._store.record_completed_tool_result(
                call,
                tool_result,
            )
            self._after_tool_result(call, tool_result)
        else:
            context.approved_scopes = frozenset()
        if tool_result.status != "completed":
            return self._handle_tool_failure(
                state,
                request,
                call,
                tool_result,
            )
        _restore_review_reference(tool_result, context)
        _restore_tool_reference(call.tool_name, tool_result, context)
        state = self._append(
            state,
            "TOOL_COMPLETED",
            {
                "idempotency_key": call.idempotency_key,
                "output_sha256": tool_result.output_sha256 or sha256_json({}),
            },
        )
        return self._after_completed_tool(
            state,
            request,
            call,
            tool_result,
            context,
        )

    def _after_completed_tool(
        self,
        state: AgentRunState,
        request: LoopRequest,
        call: ToolCall,
        tool_result: ToolResult,
        context: ToolExecutionContext,
    ) -> AgentRunResult:
        review = self._committed_review(state, request, context)
        evidence, risk, fix_plan = self._committed_role_outputs(
            state, request, context
        )
        should_finalize = (
            call.tool_name == "build_fix_plan"
            or (
                call.tool_name == "search_rule_evidence"
                and evidence is not None
                and evidence.manual_review_required
            )
            or (
                call.tool_name == "scan_project"
                and (
                    request.baseline_review is not None
                    or (
                        review.release_allowed
                        and not request.force_ai_review
                    )
                )
            )
        )
        if should_finalize:
            return self._evaluate_completed(
                state,
                request,
                review,
                evidence_output=evidence,
                risk_output=risk,
                fix_plan_output=fix_plan,
            )
        budget_error = _next_stage_budget_error(state)
        if budget_error is not None:
            return self._finalize_failure(state, budget_error)
        state = self._append(
            state,
            "EVALUATION_RECORDED",
            {
                "decision_digest": sha256_json(
                    {
                        "control": "continue",
                        "tool_name": call.tool_name,
                        "output_sha256": tool_result.output_sha256,
                    }
                ),
                "stop_reason": "workflow_stage_completed",
                "terminal_status": "CONTINUE",
            },
        )
        state = self._commit_checkpoint(state)
        return AgentRunResult(
            run_id=state.run_id,
            status="RUNNING",
            state=state,
            review=None,
            route_history=(),
            metrics=_metrics(state, stop_reason="continue"),
            trace_path=None,
        )

    def _pause_ambiguous(
        self,
        state: AgentRunState,
        call: ToolCall,
        reason: str,
        *,
        stop_reason: str = "execution_ambiguous",
    ) -> AgentRunResult:
        state = self._append(
            state,
            "RUN_PAUSED",
            {"reason": reason, "idempotency_key": call.idempotency_key},
        )
        return self._result(state, stop_reason=stop_reason)

    def _evaluate_completed(
        self,
        state: AgentRunState,
        request: LoopRequest,
        review: ReleaseReviewResult,
        *,
        evidence_output: EvidenceAgentOutput | None = None,
        risk_output: RiskAgentOutput | None = None,
        fix_plan_output: FixPlannerAgentOutput | None = None,
    ) -> AgentRunResult:
        workflow = self._evaluator.evaluate_committed(
            review=review,
            evidence_output=evidence_output,
            risk_output=risk_output,
            fix_plan_output=fix_plan_output,
            force_ai_review=request.force_ai_review,
            baseline_review=request.baseline_review,
            tracer=self._tracer,
        )
        decision_digest = _decision_digest(workflow)
        route_history = tuple(workflow.state.get("route_history", []))
        manual_review_required = workflow.state.get(
            "manual_review_required", False
        )
        stop_reason = (
            "manual_review_required"
            if manual_review_required
            else "deterministic_decision"
        )
        state = self._append(
            state,
            "EVALUATION_RECORDED",
            {
                "decision_digest": decision_digest,
                "review": review.to_dict(),
                "route_history": list(route_history),
                "manual_review_required": manual_review_required,
                "stop_reason": stop_reason,
                "terminal_status": "COMPLETED",
            },
        )
        state = self._commit_checkpoint(state)
        state = self._append(
            state,
            "RUN_COMPLETED",
            {"decision_digest": decision_digest},
        )
        return self._result(
            state,
            review=review,
            route_history=route_history,
            manual_review_required=manual_review_required,
            stop_reason=stop_reason,
        )

    def _restore_committed_references(
        self,
        state: AgentRunState,
        request: LoopRequest,
        context: ToolExecutionContext,
    ) -> None:
        for completed in (
            event
            for event in self._store.events(state.run_id)
            if event.event_kind == "TOOL_COMPLETED"
        ):
            call = self._call_for_lifecycle_event(state, request, completed)
            result = self._store.load_tool_result(call)
            if result is None or result.status != "completed":
                raise CorruptRunError(
                    "completed workflow stage has no durable result"
                )
            _restore_tool_reference(call.tool_name, result, context)

    def _committed_review(
        self,
        state: AgentRunState,
        request: LoopRequest,
        context: ToolExecutionContext,
    ) -> ReleaseReviewResult:
        result = self._latest_completed_result(state, request, "scan_project")
        if result is None:
            raise CorruptRunError("workflow has no committed scan result")
        return _review_from_result(result, context)

    def _committed_role_outputs(
        self,
        state: AgentRunState,
        request: LoopRequest,
        context: ToolExecutionContext,
    ) -> tuple[
        EvidenceAgentOutput | None,
        RiskAgentOutput | None,
        FixPlannerAgentOutput | None,
    ]:
        evidence_result = self._latest_completed_result(
            state, request, "search_rule_evidence"
        )
        risk_result = self._latest_completed_result(
            state, request, "analyze_risk"
        )
        fix_result = self._latest_completed_result(
            state, request, "build_fix_plan"
        )
        return (
            _evidence_output_from_result(evidence_result)
            if evidence_result is not None
            else None,
            _risk_output_from_result(risk_result)
            if risk_result is not None
            else None,
            _fix_plan_output_from_result(fix_result)
            if fix_result is not None
            else None,
        )

    def _latest_completed_result(
        self,
        state: AgentRunState,
        request: LoopRequest,
        tool_name: str,
    ) -> ToolResult | None:
        for completed in reversed(self._store.events(state.run_id)):
            if completed.event_kind != "TOOL_COMPLETED":
                continue
            call = self._call_for_lifecycle_event(state, request, completed)
            if call.tool_name == tool_name:
                return self._store.load_tool_result(call)
        return None

    def _next_workflow_call(
        self,
        state: AgentRunState,
        request: LoopRequest,
    ) -> ToolCall:
        events = self._store.events(state.run_id)
        latest_requested = _latest_event(events, "TOOL_REQUESTED")
        if state.evaluation_outcome == "RETRY" and latest_requested is not None:
            tool_name = str(latest_requested.payload["tool_name"])
        else:
            latest_completed = _latest_event(events, "TOOL_COMPLETED")
            if latest_completed is None:
                tool_name = "scan_project"
            else:
                completed_call = self._call_for_lifecycle_event(
                    state, request, latest_completed
                )
                next_tools = {
                    "scan_project": "search_rule_evidence",
                    "search_rule_evidence": "analyze_risk",
                    "analyze_risk": "build_fix_plan",
                }
                try:
                    tool_name = next_tools[completed_call.tool_name]
                except KeyError as exc:
                    raise CorruptRunError(
                        "completed workflow has no next registered role"
                    ) from exc
        return self._workflow_call(state, request, tool_name)

    def _workflow_call(
        self,
        state: AgentRunState,
        request: LoopRequest,
        tool_name: str,
    ) -> ToolCall:
        if tool_name == "scan_project":
            return _scan_call(state, request)
        scan_result = self._latest_completed_result(
            state, request, "scan_project"
        )
        if scan_result is None or scan_result.output is None:
            raise CorruptRunError("role call requires a committed scan result")
        review_ref = _output_string(scan_result, "review_ref")
        args: dict[str, object]
        if tool_name == "search_rule_evidence":
            args = {
                "review_ref": review_ref,
                "retrieval_mode": "hybrid",
                "top_k": 5,
                "minimum_evidence": 1,
            }
        else:
            evidence_result = self._latest_completed_result(
                state, request, "search_rule_evidence"
            )
            if evidence_result is None:
                raise CorruptRunError(
                    "risk or fix role requires committed evidence"
                )
            evidence_ref = _output_string(evidence_result, "evidence_ref")
            if tool_name == "analyze_risk":
                args = {
                    "review_ref": review_ref,
                    "evidence_ref": evidence_ref,
                }
            elif tool_name == "build_fix_plan":
                risk_result = self._latest_completed_result(
                    state, request, "analyze_risk"
                )
                if risk_result is None:
                    raise CorruptRunError(
                        "fix role requires committed risk analysis"
                    )
                args = {
                    "review_ref": review_ref,
                    "evidence_ref": evidence_ref,
                    "risk_ref": _output_string(risk_result, "risk_ref"),
                }
            else:
                raise CorruptRunError(f"unknown workflow role tool: {tool_name}")
        return ToolCall.create(
            tool_name=tool_name,
            tool_version="1",
            args=args,
            run_id=state.run_id,
            step_index=state.step_index,
            idempotency_key=f"{state.run_id}:{state.step_index}:{tool_name}",
        )

    def _handle_tool_failure(
        self,
        state: AgentRunState,
        request: LoopRequest,
        call: ToolCall,
        tool_result: ToolResult,
    ) -> AgentRunResult:
        retryable = (
            state.retry_count
            < min(state.budget.max_retries, spec.max_retries)
            and self._evaluator.is_retryable_failure(tool_result.error_type)
        ) if (spec := self._registry.get(call.tool_name, call.tool_version)) else False
        state = self._append(
            state,
            "TOOL_FAILED",
            {
                "idempotency_key": call.idempotency_key,
                "retryable": retryable,
                "error_type": tool_result.error_type or "tool_failed",
                "failure_phase": "execution",
            },
        )
        if not retryable:
            return self._finalize_failure(
                state, tool_result.error_type or "tool_failed"
            )
        retry_budget_error = _retry_budget_error(state)
        if retry_budget_error is not None:
            return self._finalize_failure(state, retry_budget_error)
        state = self._record_retry_evaluation(
            state, tool_result.error_type or "tool_failed"
        )
        state = self._commit_checkpoint(state)
        return AgentRunResult(
            run_id=state.run_id,
            status="RUNNING",
            state=state,
            review=None,
            route_history=(),
            metrics=_metrics(state, stop_reason="retry"),
            trace_path=None,
        )

    def _record_retry_evaluation(
        self,
        state: AgentRunState,
        error_type: str,
    ) -> AgentRunState:
        decision_digest = sha256_json(
            {
                "control": "retry",
                "error_type": error_type,
                "retry_count": state.retry_count,
            }
        )
        return self._append(
            state,
            "EVALUATION_RECORDED",
            {
                "decision_digest": decision_digest,
                "stop_reason": "retry",
                "terminal_status": "RETRY",
            },
        )

    def _record_nonretryable_failure(
        self,
        state: AgentRunState,
        call: ToolCall,
        error_type: str,
        *,
        failure_phase: str = "guardrail_denied",
    ) -> AgentRunResult:
        state = self._append(
            state,
            "TOOL_FAILED",
            {
                "idempotency_key": call.idempotency_key,
                "retryable": False,
                "error_type": error_type,
                "failure_phase": failure_phase,
            },
        )
        return self._finalize_failure(state, error_type)

    def _finalize_failure(
        self,
        state: AgentRunState,
        error_type: str,
    ) -> AgentRunResult:
        decision_digest = sha256_json(
            {
                "control": "fail",
                "error_type": error_type,
                "step_index": state.step_index,
            }
        )
        state = self._append(
            state,
            "EVALUATION_RECORDED",
            {
                "decision_digest": decision_digest,
                "stop_reason": error_type,
                "terminal_status": "FAILED",
            },
        )
        state = self._commit_checkpoint(state)
        state = self._append(
            state,
            "RUN_FAILED",
            {"decision_digest": decision_digest, "error_type": error_type},
        )
        return self._result(state, stop_reason=error_type)

    def _fail_budget(
        self,
        state: AgentRunState,
        error_type: str,
    ) -> AgentRunResult:
        decision_digest = sha256_json(
            {
                "control": "fail",
                "error_type": error_type,
                "step_index": state.step_index,
                "tool_call_count": state.tool_call_count,
            }
        )
        state = self._append(
            state,
            "EVALUATION_RECORDED",
            {
                "decision_digest": decision_digest,
                "stop_reason": error_type,
                "terminal_status": "FAILED",
            },
        )
        state = self._append(
            state,
            "RUN_FAILED",
            {"decision_digest": decision_digest, "error_type": error_type},
        )
        return self._result(state, stop_reason=error_type)

    def _commit_checkpoint(self, state: AgentRunState) -> AgentRunState:
        next_step = state.step_index + 1
        return self._append(
            state,
            "CHECKPOINT_COMMITTED",
            {
                "checkpoint_event_id": (
                    f"checkpoint:{state.run_id}:{next_step}"
                ),
                "step_index": next_step,
            },
        )

    def _context(self, state: AgentRunState) -> ToolExecutionContext:
        context = self._contexts.get(state.run_id)
        if context is None:
            charged_calls = state.tool_call_count - int(
                state.pending_tool_idempotency_key is not None
            )
            context = ToolExecutionContext(
                budget=state.budget,
                tool_call_count=max(charged_calls, 0),
                retry_count=0,
            )
            self._contexts[state.run_id] = context
        return context

    def _resume_without_pending(
        self,
        state: AgentRunState,
        request: LoopRequest,
        context: ToolExecutionContext,
    ) -> AgentRunResult:
        events = self._store.events(state.run_id)
        completed = _latest_event(events, "TOOL_COMPLETED")
        failed = _latest_event(events, "TOOL_FAILED")
        evaluated = _latest_event(events, "EVALUATION_RECORDED")
        checkpoint = _latest_event(events, "CHECKPOINT_COMMITTED")
        if completed is not None and (
            evaluated is None or completed.sequence > evaluated.sequence
        ):
            call = self._call_for_completed_event(state, request, completed)
            tool_result = self._store.load_tool_result(call)
            if tool_result is None or tool_result.status != "completed":
                return self._finalize_failure(
                    state, "durable_tool_result_missing"
                )
            _restore_tool_reference(call.tool_name, tool_result, context)
            return self._after_completed_tool(
                state,
                request,
                call,
                tool_result,
                context,
            )
        if failed is not None and (
            evaluated is None or failed.sequence > evaluated.sequence
        ):
            call = self._call_for_lifecycle_event(state, request, failed)
            tool_result = self._store.load_tool_result(call)
            error_type = (
                tool_result.error_type
                if tool_result is not None and tool_result.error_type is not None
                else str(failed.payload.get("error_type", "tool_failed"))
            )
            if failed.payload.get("retryable") is True:
                state = self._record_retry_evaluation(state, error_type)
                state = self._commit_checkpoint(state)
                budget_error = _budget_error(state)
                if budget_error is not None:
                    return self._fail_budget(state, budget_error)
                return self._drive(state, request)
            return self._finalize_failure(state, error_type)
        if evaluated is not None and (
            checkpoint is None or evaluated.sequence > checkpoint.sequence
        ):
            budget_error = _budget_error(state)
            if (
                evaluated.payload.get("terminal_status") == "FAILED"
                and budget_error is not None
                and evaluated.payload.get("stop_reason") == budget_error
            ):
                return self._finish_recorded_evaluation(state, evaluated)
            state = self._commit_checkpoint(state)
            if evaluated.payload.get("terminal_status") == "RETRY":
                return self._drive(state, request)
            return self._finish_recorded_evaluation(state, evaluated)
        if (
            evaluated is not None
            and checkpoint is not None
            and checkpoint.sequence > evaluated.sequence
            and evaluated.payload.get("terminal_status") in {"COMPLETED", "FAILED"}
        ):
            return self._finish_recorded_evaluation(state, evaluated)
        return self._drive(state, request)

    def _validate_completed_results(
        self,
        state: AgentRunState,
        request: LoopRequest,
    ) -> None:
        for reconciled in (
            event
            for event in self._store.events(state.run_id)
            if event.event_kind == "TOOL_RESULT_RECONCILED"
        ):
            call = self._call_for_lifecycle_event(state, request, reconciled)
            tool_result = self._store.load_tool_result(call)
            if tool_result is None:
                raise CorruptRunError(
                    "TOOL_RESULT_RECONCILED has no durable tool result"
                )
            if reconciled.payload["result_digest"] != _tool_result_digest(
                tool_result
            ):
                raise CorruptRunError(
                    "reconciled result digest does not match durable tool result"
                )
        for completed in (
            event
            for event in self._store.events(state.run_id)
            if event.event_kind == "TOOL_COMPLETED"
        ):
            call = self._call_for_completed_event(state, request, completed)
            tool_result = self._store.load_tool_result(call)
            if tool_result is None or tool_result.status != "completed":
                raise CorruptRunError(
                    "TOOL_COMPLETED has no completed durable tool result"
                )
            if completed.payload["output_sha256"] != tool_result.output_sha256:
                raise CorruptRunError(
                    "TOOL_COMPLETED output hash does not match durable tool result"
                )

    def _finish_recorded_evaluation(
        self,
        state: AgentRunState,
        evaluated: RunEvent,
    ) -> AgentRunResult:
        decision_digest = str(evaluated.payload["decision_digest"])
        terminal_status = evaluated.payload.get("terminal_status")
        stop_reason = str(evaluated.payload.get("stop_reason", "recovered"))
        event_kind = "RUN_FAILED" if terminal_status == "FAILED" else "RUN_COMPLETED"
        payload: dict[str, object] = {"decision_digest": decision_digest}
        if event_kind == "RUN_FAILED":
            payload["error_type"] = stop_reason
        state = self._append(state, event_kind, payload)
        return self._materialized_result(state, evaluated)

    def _call_for_completed_event(
        self,
        state: AgentRunState,
        request: LoopRequest,
        completed: RunEvent,
    ) -> ToolCall:
        return self._call_for_lifecycle_event(state, request, completed)

    def _call_for_lifecycle_event(
        self,
        state: AgentRunState,
        request: LoopRequest,
        lifecycle_event: RunEvent,
    ) -> ToolCall:
        requested = next(
            event
            for event in reversed(self._store.events(state.run_id))
            if event.event_kind == "TOOL_REQUESTED"
            and event.sequence < lifecycle_event.sequence
        )
        return _call_from_requested_event(state, request, requested)

    def _pending_call(
        self,
        state: AgentRunState,
        request: LoopRequest,
    ) -> ToolCall:
        requested = next(
            event
            for event in reversed(self._store.events(state.run_id))
            if event.event_kind == "TOOL_REQUESTED"
        )
        return _call_from_requested_event(state, request, requested)

    def _request_from_events(self, state: AgentRunState) -> LoopRequest:
        force_ai_review = False
        baseline_review: ReleaseReviewResult | None = None
        events = self._store.events(state.run_id)
        request_payload: Mapping[str, object] | None = None
        for event in events:
            if event.event_kind == "RUN_CREATED" and isinstance(
                event.payload.get("request"), Mapping
            ):
                request_payload = event.payload["request"]
            if event.event_kind == "PLAN_PROPOSED":
                request_payload = event.payload
        if request_payload is not None:
            force_ai_review = bool(
                request_payload.get("force_ai_review", False)
            )
            raw_baseline = request_payload.get("baseline_review")
            if isinstance(raw_baseline, Mapping):
                baseline_review = _review_from_report(raw_baseline)
        request = LoopRequest(
            project_path=state.project_root,
            task_kind=state.task_kind,
            budget=state.budget,
            force_ai_review=force_ai_review,
            baseline_review=baseline_review,
        )
        self._requests[state.run_id] = request
        return request

    def _append(
        self,
        state: AgentRunState,
        event_kind: str,
        payload: Mapping[str, object],
    ) -> AgentRunState:
        event = self._store.append(
            state.run_id,
            state.last_event_sequence,
            event_kind,
            payload,
        )
        self._trace_event(event)
        self._after_event(event)
        return self._store.load_state(state.run_id)

    def _trace_event(self, event: RunEvent) -> None:
        if self._tracer is None:
            return
        payload = event.payload
        raw_step = payload.get("step_index")
        if raw_step is None and event.event_kind == "RUN_CREATED":
            raw_state = payload.get("state")
            if isinstance(raw_state, Mapping):
                raw_step = raw_state.get("step_index")
        step_index = (
            raw_step
            if isinstance(raw_step, int) and not isinstance(raw_step, bool)
            else None
        )
        raw_key = payload.get("idempotency_key")
        raw_approval = payload.get("approval_id")
        if event.event_kind in {"APPROVAL_REQUESTED", "APPROVAL_DECIDED"}:
            envelope_key = (
                "request"
                if event.event_kind == "APPROVAL_REQUESTED"
                else "grant"
            )
            envelope = payload.get(envelope_key)
            if isinstance(envelope, Mapping):
                raw_key = envelope.get("idempotency_key")
                raw_approval = envelope.get("approval_id")
        self._tracer.runtime_event(
            run_id=event.run_id,
            event_kind=event.event_kind,
            event_sequence=event.sequence,
            step_index=step_index,
            idempotency_key=(str(raw_key) if raw_key is not None else None),
            approval_id=(
                str(raw_approval) if raw_approval is not None else None
            ),
            checkpoint_sequence=(
                event.sequence
                if event.event_kind == "CHECKPOINT_COMMITTED"
                else None
            ),
            occurred_at=event.created_at_utc,
        )

    def _rebuild_trace(self, state: AgentRunState) -> None:
        """Reconstruct crash-safe runtime observations from committed events."""

        if self._tracer is None:
            return
        events = self._store.events(state.run_id)
        requests: dict[str, RunEvent] = {}
        acceptances: dict[str, RunEvent] = {}
        for event in events:
            if event.event_kind == "TOOL_STARTED":
                key = str(event.payload["idempotency_key"])
                requested = requests[key]
                accepted = acceptances.get(key)
                self._trace_guardrail_from_event(
                    requested,
                    GuardrailDecision.ALLOW,
                    ordering_event_sequence=(
                        accepted.sequence if accepted is not None else None
                    ),
                    occurred_at=(
                        accepted.created_at_utc
                        if accepted is not None
                        else requested.created_at_utc
                    ),
                )
            elif event.event_kind == "TOOL_FAILED" and (
                event.payload.get("failure_phase") == "guardrail_denied"
            ):
                key = str(event.payload["idempotency_key"])
                requested = requests[key]
                decision = (
                    GuardrailDecision.QUARANTINE
                    if event.payload.get("error_type")
                    == "sensitive_path_argument"
                    else GuardrailDecision.BLOCK
                )
                self._trace_guardrail_from_event(
                    requested,
                    decision,
                    occurred_at=requested.created_at_utc,
                )
            self._trace_event(event)
            if event.event_kind == "TOOL_REQUESTED":
                key = str(event.payload["idempotency_key"])
                requests[key] = event
                if event.payload.get("requires_approval") is True:
                    self._trace_guardrail_from_event(
                        event,
                        GuardrailDecision.REQUIRE_HITL,
                        occurred_at=event.created_at_utc,
                    )
            elif event.event_kind == "APPROVAL_DECIDED":
                grant = event.payload.get("grant")
                if isinstance(grant, Mapping):
                    key = str(grant["idempotency_key"])
                    acceptances[key] = event

    def _trace_guardrail_from_event(
        self,
        requested: RunEvent,
        decision: GuardrailDecision,
        *,
        ordering_event_sequence: int | None = None,
        occurred_at: str,
    ) -> None:
        if self._tracer is None:
            return
        raw_step = requested.payload.get("step_index")
        step_index = (
            raw_step
            if isinstance(raw_step, int) and not isinstance(raw_step, bool)
            else 0
        )
        raw_approval = requested.payload.get("approval_id")
        self._tracer.guardrail(
            run_id=requested.run_id,
            step_index=step_index,
            idempotency_key=str(requested.payload["idempotency_key"]),
            decision=decision.value,
            approval_id=(
                str(raw_approval) if raw_approval is not None else None
            ),
            event_sequence=requested.sequence,
            ordering_event_sequence=ordering_event_sequence,
            occurred_at=occurred_at,
        )

    def _after_event(self, event: RunEvent) -> None:
        """Test seam for simulating a process crash after a durable append."""

    def _after_tool_result(self, call: ToolCall, result: ToolResult) -> None:
        """Test seam after a result is durable and before lifecycle completion."""

    def _after_tool_execution(self, call: ToolCall, result: ToolResult) -> None:
        """Test seam after handler return but before its result is durable."""

    def _terminal_result(self, state: AgentRunState) -> AgentRunResult:
        evaluated = _latest_event(
            self._store.events(state.run_id), "EVALUATION_RECORDED"
        )
        if evaluated is None:
            return AgentRunResult(
                run_id=state.run_id,
                status=state.status,
                state=state,
                review=None,
                route_history=(),
                metrics=_metrics(state, stop_reason="terminal"),
                trace_path=_existing_trace_path(self._store, state.run_id),
            )
        return self._materialized_result(state, evaluated)

    def _materialized_result(
        self,
        state: AgentRunState,
        evaluated: RunEvent,
    ) -> AgentRunResult:
        raw_review = evaluated.payload.get("review")
        review = (
            _review_from_report(raw_review)
            if isinstance(raw_review, Mapping)
            else None
        )
        routes = evaluated.payload.get("route_history", ())
        route_history = (
            tuple(str(route) for route in routes)
            if isinstance(routes, (list, tuple))
            else ()
        )
        stop_reason = str(evaluated.payload.get("stop_reason", "terminal"))
        manual = bool(evaluated.payload.get("manual_review_required", False))
        result = AgentRunResult(
            run_id=state.run_id,
            status=state.status,
            state=state,
            review=review,
            route_history=route_history,
            metrics=_metrics(
                state,
                stop_reason=stop_reason,
                manual_review_required=manual,
            ),
            trace_path=_existing_trace_path(self._store, state.run_id),
        )
        self._results[state.run_id] = result
        return result

    def _result(
        self,
        state: AgentRunState,
        *,
        review: ReleaseReviewResult | None = None,
        route_history: tuple[str, ...] = (),
        manual_review_required: bool = False,
        stop_reason: str,
    ) -> AgentRunResult:
        trace_path: Path | None = None
        if self._tracer is not None:
            trace_path = self._tracer.write(
                self._store.root / "traces" / state.run_id
            ).trace_path
        result = AgentRunResult(
            run_id=state.run_id,
            status=state.status,
            state=state,
            review=review,
            route_history=route_history,
            metrics=_metrics(
                state,
                stop_reason=stop_reason,
                manual_review_required=manual_review_required,
            ),
            trace_path=trace_path,
        )
        self._results[state.run_id] = result
        return result


def _plan_document(request: LoopRequest) -> dict[str, object]:
    return {
        "project_path": str(request.project_path),
        "task_kind": request.task_kind,
        "force_ai_review": request.force_ai_review,
        "baseline_review": (
            request.baseline_review.to_dict()
            if request.baseline_review is not None
            else None
        ),
    }


def _scan_call(
    state: AgentRunState,
    request: LoopRequest,
    *,
    idempotency_key: str | None = None,
) -> ToolCall:
    return ToolCall.create(
        tool_name="scan_project",
        tool_version="1",
        args={
            "project_path": str(request.project_path),
            "include_pytest_execution": False,
        },
        run_id=state.run_id,
        step_index=state.step_index,
        idempotency_key=idempotency_key
        or f"{state.run_id}:{state.step_index}:scan_project",
    )


def _call_from_requested_event(
    state: AgentRunState,
    request: LoopRequest,
    requested: RunEvent,
) -> ToolCall:
    payload = requested.payload
    if {
        "tool_version",
        "canonical_args",
        "args_sha256",
    }.issubset(payload):
        return ToolCall(
            tool_name=str(payload["tool_name"]),
            tool_version=str(payload["tool_version"]),
            canonical_args=str(payload["canonical_args"]),
            args_sha256=str(payload["args_sha256"]),
            run_id=state.run_id,
            step_index=(
                int(payload["step_index"])
                if isinstance(payload.get("step_index"), int)
                and not isinstance(payload.get("step_index"), bool)
                else state.step_index
            ),
            idempotency_key=str(payload["idempotency_key"]),
        )
    return _scan_call(
        state,
        request,
        idempotency_key=str(payload["idempotency_key"]),
    )
def _review_from_result(
    result: ToolResult,
    context: ToolExecutionContext,
) -> ReleaseReviewResult:
    output = result.output
    if output is None:
        raise ValueError("completed scan result has no output")
    review_ref = output.get("review_ref")
    review = context.references.get(review_ref) if isinstance(review_ref, str) else None
    if not isinstance(review, ReleaseReviewResult):
        raise ValueError("completed scan result has no retained review")
    return review


def _restore_review_reference(
    result: ToolResult,
    context: ToolExecutionContext,
) -> None:
    output = result.output
    if output is None:
        return
    review_ref = output.get("review_ref")
    report = output.get("report")
    if (
        isinstance(review_ref, str)
        and isinstance(report, Mapping)
    ):
        context.references[review_ref] = _review_from_report(report)


def _restore_tool_reference(
    tool_name: str,
    result: ToolResult,
    context: ToolExecutionContext,
) -> None:
    if tool_name == "scan_project":
        _restore_review_reference(result, context)
        return
    if result.output is None:
        raise CorruptRunError(
            f"completed {tool_name} result has no durable output"
        )
    if tool_name == "search_rule_evidence":
        evidence_output = _evidence_output_from_result(result)
        context.references[_output_string(result, "evidence_ref")] = (
            evidence_output.evidence
        )
    elif tool_name == "analyze_risk":
        risk_output = _risk_output_from_result(result)
        context.references[_output_string(result, "risk_ref")] = risk_output


def _evidence_output_from_result(result: ToolResult) -> EvidenceAgentOutput:
    raw = _output_mapping(result, "evidence_output")
    try:
        return EvidenceAgentOutput.from_dict(raw)
    except ValueError as exc:
        raise CorruptRunError("durable evidence output is invalid") from exc


def _risk_output_from_result(result: ToolResult) -> RiskAgentOutput:
    raw = _output_mapping(result, "risk_output")
    try:
        return RiskAgentOutput.from_dict(raw)
    except ValueError as exc:
        raise CorruptRunError("durable risk output is invalid") from exc


def _fix_plan_output_from_result(result: ToolResult) -> FixPlannerAgentOutput:
    raw = _output_mapping(result, "fix_plan_output")
    try:
        return FixPlannerAgentOutput.from_dict(raw)
    except ValueError as exc:
        raise CorruptRunError("durable fix-plan output is invalid") from exc


def _output_mapping(result: ToolResult, key: str) -> Mapping[str, object]:
    if result.output is None:
        raise CorruptRunError("completed tool result has no output")
    value = result.output.get(key)
    if not isinstance(value, Mapping):
        raise CorruptRunError(f"completed tool result is missing {key}")
    return value


def _output_string(result: ToolResult, key: str) -> str:
    if result.output is None:
        raise CorruptRunError("completed tool result has no output")
    value = result.output.get(key)
    if not isinstance(value, str) or not value:
        raise CorruptRunError(f"completed tool result is missing {key}")
    return value


def _review_from_report(report: Mapping[str, object]) -> ReleaseReviewResult:
    plain = _plain_json(report)
    if not isinstance(plain, dict):
        raise ValueError("review report must be an object")
    raw_results = plain.get("results")
    raw_evidence = plain.get("retrieval_evidence")
    summary = plain.get("summary")
    if not isinstance(raw_results, list) or not isinstance(raw_evidence, list):
        raise ValueError("review report is missing results or evidence")
    if not isinstance(summary, dict):
        raise ValueError("review report is missing summary")
    check_results = tuple(
        CheckResult(
            checker_name=str(item["checker_name"]),
            status=CheckStatus(str(item["status"])),
            risk_level=RiskLevel(str(item["risk_level"])),
            title=str(item["title"]),
            message=str(item["message"]),
            evidence=[str(value) for value in item.get("evidence", [])],
            recommendation=(
                str(item["recommendation"])
                if item.get("recommendation") is not None
                else None
            ),
            rule_id=str(item["rule_id"]) if item.get("rule_id") else None,
            rule_source=(
                str(item["rule_source"]) if item.get("rule_source") else None
            ),
            file_path=(str(item["file_path"]) if item.get("file_path") else None),
            metadata=dict(item.get("metadata", {})),
        )
        for item in raw_results
        if isinstance(item, dict)
    )
    evidence = tuple(
        RetrievalEvidence(
            evidence_id=str(item["evidence_id"]),
            rule_id=str(item["rule_id"]),
            source_url=str(item["source_url"]),
            local_source=str(item["local_source"]),
            chunk_id=str(item["chunk_id"]),
            retrieval_method=str(item["retrieval_method"]),
            raw_score=float(item["raw_score"]),
            fusion_score=float(item["fusion_score"]),
            rerank_score=float(item["rerank_score"]),
            text=str(item["text"]),
            metadata={
                str(key): str(value)
                for key, value in dict(item.get("metadata", {})).items()
            },
        )
        for item in raw_evidence
        if isinstance(item, dict)
    )
    return ReleaseReviewResult(
        project_path=Path(str(plain["project_path"])),
        include_pytest_execution=bool(plain["include_pytest_execution"]),
        check_results=check_results,
        summary=summary,
        report_payload=plain,
        advice_result=None,
        retrieval_evidence=evidence,
        artifacts=ReleaseReviewArtifacts(),
    )


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _tool_result_digest(result: ToolResult) -> str:
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


def _decision_digest(workflow: ReleaseAgentWorkflowResult) -> str:
    review = workflow.review
    return sha256_json(
        {
            "release_allowed": review.release_allowed,
            "findings": [
                {
                    "rule_id": finding.rule_id,
                    "status": finding.status.value,
                    "risk_level": finding.risk_level.value,
                    "should_block_release": finding.should_block_release,
                }
                for finding in review.check_results
            ],
            "evidence_ids": sorted(
                item.evidence_id for item in review.retrieval_evidence
            ),
            "manual_review_required": workflow.state.get(
                "manual_review_required", False
            ),
        }
    )


def _checkpoint_digest(state: AgentRunState) -> str:
    return sha256_json(
        {
            "checkpoint_event_id": state.checkpoint_event_id,
            "step_index": state.step_index,
        }
    )


def _budget_error(state: AgentRunState) -> str | None:
    if state.step_index >= state.budget.max_steps:
        return "step_budget_exhausted"
    if state.tool_call_count >= state.budget.max_tool_calls:
        return "tool_call_budget_exhausted"
    return None


def _retry_budget_error(state: AgentRunState) -> str | None:
    """Return the budget that would prevent the next retry from starting."""
    if state.step_index + 1 >= state.budget.max_steps:
        return "step_budget_exhausted"
    if state.tool_call_count >= state.budget.max_tool_calls:
        return "tool_call_budget_exhausted"
    return None


def _next_stage_budget_error(state: AgentRunState) -> str | None:
    """Return the budget that would prevent a distinct next role stage."""
    if state.step_index + 1 >= state.budget.max_steps:
        return "step_budget_exhausted"
    if state.tool_call_count >= state.budget.max_tool_calls:
        return "tool_call_budget_exhausted"
    return None


def _metrics(
    state: AgentRunState,
    *,
    stop_reason: str,
    manual_review_required: bool = False,
) -> dict[str, object]:
    return {
        "steps": state.step_index,
        "tool_calls": state.tool_call_count,
        "retries": state.retry_count,
        "manual_review_required": manual_review_required,
        "stop_reason": stop_reason,
    }


def _approval_grant(
    approval: object | None,
    *,
    expected_request: ApprovalRequest,
) -> ApprovalGrant | None:
    try:
        grant = (
            approval
            if isinstance(approval, ApprovalGrant)
            else ApprovalGrant.from_dict(approval)
            if isinstance(approval, Mapping)
            else None
        )
        if grant is None:
            return None
        grant.verify_for(expected_request, at_utc=_utc_now())
    except ValueError:
        return None
    return grant


def _approval_request(call: ToolCall, spec: ToolSpec) -> ApprovalRequest:
    scope = spec.required_approval_scope
    if scope is None:
        raise ValueError("approval-gated tool spec is missing its approval scope")
    now = datetime.now(timezone.utc)
    return ApprovalRequest.issue(
        run_id=call.run_id,
        tool_name=call.tool_name,
        tool_version=call.tool_version,
        args_sha256=call.args_sha256,
        allowed_paths=_affected_paths(call.args),
        scope=scope,
        step_index=call.step_index,
        idempotency_key=call.idempotency_key,
        issued_at_utc=now.isoformat(),
        expires_at_utc=(now + timedelta(minutes=10)).isoformat(),
        requester_identity="releaseguard.runtime",
        identity_evidence="local-durable-runtime",
    )


def _affected_paths(arguments: object) -> tuple[str, ...]:
    paths: list[str] = []
    if isinstance(arguments, Mapping):
        for key, value in arguments.items():
            if isinstance(value, str) and (
                key == "project_path" or key.endswith("_path")
            ):
                paths.append(str(Path(value).expanduser().resolve()))
            else:
                paths.extend(_affected_paths(value))
    elif isinstance(arguments, (list, tuple)):
        for value in arguments:
            paths.extend(_affected_paths(value))
    return tuple(sorted(set(paths)))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_event(events: tuple[RunEvent, ...], event_kind: str) -> RunEvent | None:
    return next(
        (event for event in reversed(events) if event.event_kind == event_kind),
        None,
    )


def _existing_trace_path(store: AgentRunStore, run_id: str) -> Path | None:
    trace_path = store.root / "traces" / run_id / "execution_trace.json"
    return trace_path if trace_path.is_file() else None
