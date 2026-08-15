import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from llama_index.core.embeddings import BaseEmbedding

from releaseguard_agent.agents import (
    ReleaseRiskAnalysisAgent,
    ReleaseRiskAnalysisContext,
)
from releaseguard_agent.llm import FakeLLMClient
from releaseguard_agent.rag import RuleRetrievalService, get_default_rule_index_path
from releaseguard_agent.services import (
    ReleaseReviewService,
    build_agent_advice_result,
)
from releaseguard_agent.services.agent_workflow_service import (
    ReleaseAgentWorkflowService,
)
from releaseguard_agent.services.verification_service import (
    ReleaseVerificationService,
)
from releaseguard_agent.runtime import (
    AgentRunState,
    LoopController,
    LoopRequest,
    RunBudget,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolSpec,
    sha256_json,
)
from releaseguard_agent.runtime.models import AgentRunStatus, RunEvent
from releaseguard_agent.runtime.store import AgentRunStore, CorruptRunError
from releaseguard_agent.observability import ExecutionTracer


_RUNTIME_THRESHOLDS: dict[str, float] = {
    "tool_call_validity": 0.5,
    "guardrail_precision": 0.6,
    "resume_success_rate": 1.0,
    "duplicate_side_effect_rate": 0.0,
    "hitl_gate_recall": 1.0,
    "loop_termination_rate": 1.0,
}
_RUNTIME_LIMITATIONS = (
    "Guardrail precision is measured on five deterministic policy fixtures; "
    "the 0.60 floor is a regression threshold, not a production quality claim.",
    "Runtime fixtures use offline deterministic handlers and do not measure "
    "provider reliability or semantic model quality.",
)


class DeterministicEvaluationEmbedding(BaseEmbedding):
    """Fixed offline embedding for integration repeatability, not quality claims."""

    def _vector(self, text: str) -> list[float]:
        normalized = text.lower()
        return [
            float(normalized.count("docker")),
            float(normalized.count("pytest") + normalized.count("test")),
            float(normalized.count("fastapi")),
            float(normalized.count("dependency")),
            float(normalized.count("from")),
            float(normalized.count("base image")),
            1.0,
        ]

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._vector(text)


@dataclass(frozen=True)
class EvaluationResult:
    dataset: str
    metrics: dict[str, float]
    details: dict[str, Any]
    checks_passed: bool | None = None

    @property
    def passed(self) -> bool:
        if self.checks_passed is not None:
            return self.checks_passed
        return all(value == 1.0 for value in self.metrics.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "metrics": dict(self.metrics),
            "passed": self.passed,
            "details": dict(self.details),
            "limitations": [
                "Fixed fake embeddings validate plumbing, not semantic quality.",
                "FakeLLM validates schema handling, not provider answer quality.",
            ],
        }


@dataclass(frozen=True)
class RuntimeReplayResult:
    """Validated observations reconstructed only from a durable event log."""

    run_id: str
    final_status: AgentRunStatus
    decision_digest: str | None
    route_history: tuple[str, ...]
    idempotency_outcomes: tuple[tuple[str, str, str | None], ...]
    checkpoint_sequence: int | None
    event_digest: str


class EvaluationRunner:
    """Run deterministic golden cases against real product entry services."""

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = (
            Path(project_root).resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[3]
        )
        self._review = ReleaseReviewService()

    def run(self, dataset_path: Path) -> EvaluationResult:
        normalized = Path(dataset_path).expanduser().resolve()
        payload = json.loads(normalized.read_text(encoding="utf-8"))
        if "runtime_cases" in payload:
            runtime = self._evaluate_runtime(payload["runtime_cases"])
            return EvaluationResult(
                dataset=str(normalized),
                metrics=runtime["metrics"],
                details={
                    "runtime": {
                        "denominators": runtime["denominators"],
                        "thresholds": runtime["thresholds"],
                        "threshold_results": runtime["threshold_results"],
                        "limitations": runtime["limitations"],
                        "cases": runtime["cases"],
                    }
                },
                checks_passed=all(
                    case["matched"] for case in runtime["cases"]
                ) and all(runtime["threshold_results"].values()),
            )
        retrieval = self._evaluate_retrieval(payload["retrieval_cases"])
        decisions = self._evaluate_decisions(payload["decision_cases"])
        paths = self._evaluate_paths(payload["graph_cases"])
        llm_validity = self._evaluate_llm(payload["llm_cases"])
        delta = self._evaluate_verification(payload["verification_cases"])
        metrics = {
            "recall_at_k": retrieval["recall_at_k"],
            "evidence_source_accuracy": retrieval["source_accuracy"],
            "deterministic_decision_consistency": decisions["accuracy"],
            "llm_structured_output_valid_rate": llm_validity["valid_rate"],
            "graph_path_coverage": paths["coverage"],
            "before_after_delta_accuracy": delta["accuracy"],
        }
        return EvaluationResult(
            dataset=str(normalized),
            metrics=metrics,
            details={
                "retrieval": retrieval["cases"],
                "decisions": decisions["cases"],
                "graph_paths": paths["cases"],
                "llm": llm_validity["cases"],
                "verification": delta["cases"],
            },
        )

    def replay_runtime_run(
        self,
        store: AgentRunStore,
        run_id: str,
    ) -> RuntimeReplayResult:
        """Validate and summarize a run without invoking any tool or graph."""

        state = store.load_state(run_id)
        events = store.events(run_id)
        evaluated = next(
            (
                event
                for event in reversed(events)
                if event.event_kind == "EVALUATION_RECORDED"
            ),
            None,
        )
        routes = evaluated.payload.get("route_history", ()) if evaluated else ()
        route_history = (
            tuple(str(route) for route in routes)
            if isinstance(routes, (list, tuple))
            else ()
        )
        calls: dict[str, ToolCall] = {}
        for event in events:
            if event.event_kind != "TOOL_REQUESTED":
                continue
            try:
                call = ToolCall(
                    tool_name=str(event.payload["tool_name"]),
                    tool_version=str(event.payload["tool_version"]),
                    canonical_args=str(event.payload["canonical_args"]),
                    args_sha256=str(event.payload["args_sha256"]),
                    run_id=run_id,
                    step_index=int(event.payload["step_index"]),
                    idempotency_key=str(event.payload["idempotency_key"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise CorruptRunError(
                    "replay tool request metadata is invalid"
                ) from exc
            calls[call.idempotency_key] = call
        outcomes: list[tuple[str, str, str | None]] = []
        for event in events:
            if event.event_kind == "TOOL_COMPLETED":
                key = str(event.payload["idempotency_key"])
                stored_call = calls.get(key)
                result = (
                    store.load_tool_result(stored_call)
                    if stored_call is not None
                    else None
                )
                if (
                    result is None
                    or result.status != "completed"
                    or result.output_sha256 != event.payload["output_sha256"]
                ):
                    raise CorruptRunError(
                        "replay idempotency outcome does not match tool ledger"
                    )
                outcomes.append(
                    (
                        key,
                        "completed",
                        result.output_sha256,
                    )
                )
            elif event.event_kind == "TOOL_FAILED":
                key = str(event.payload["idempotency_key"])
                error_type = event.payload.get("error_type")
                failure_phase = event.payload.get("failure_phase")
                stored_call = calls.get(key)
                result = (
                    store.load_tool_result(stored_call)
                    if stored_call is not None
                    else None
                )
                if failure_phase == "guardrail_denied":
                    if result is not None:
                        raise CorruptRunError(
                            "guardrail denial must not have a tool ledger row"
                        )
                elif failure_phase == "execution":
                    if result is None or result.status not in {
                        "error",
                        "blocked",
                        "quarantined",
                    }:
                        raise CorruptRunError(
                            "execution failure is missing a terminal ledger row"
                        )
                elif failure_phase == "uncertain_execution":
                    if result is not None:
                        raise CorruptRunError(
                            "uncertain execution must not have a ledger row"
                        )
                else:
                    raise CorruptRunError(
                        "replay failure phase is missing or invalid"
                    )
                if result is not None and result.error_type != error_type:
                    raise CorruptRunError(
                        "replay failure does not match idempotency ledger"
                    )
                outcomes.append(
                    (
                        key,
                        "failed",
                        str(error_type) if error_type is not None else None,
                    )
                )
            elif event.event_kind == "RUN_PAUSED" and str(
                event.payload.get("reason", "")
            ) in {
                "execution_attempt_unresolved",
                "execution_timeout_ambiguous",
            }:
                key = str(event.payload["idempotency_key"])
                stored_call = calls.get(key)
                result = (
                    store.load_tool_result(stored_call)
                    if stored_call is not None
                    else None
                )
                if result is not None:
                    raise CorruptRunError(
                        "ambiguous execution must not have a tool ledger row"
                    )
                outcomes.append(
                    (key, "ambiguous", str(event.payload["reason"]))
                )
        checkpoint_sequence = next(
            (
                event.sequence
                for event in reversed(events)
                if event.event_kind == "CHECKPOINT_COMMITTED"
            ),
            None,
        )
        return RuntimeReplayResult(
            run_id=run_id,
            final_status=state.status,
            decision_digest=state.decision_digest,
            route_history=route_history,
            idempotency_outcomes=tuple(outcomes),
            checkpoint_sequence=checkpoint_sequence,
            event_digest=sha256_json([event.to_dict() for event in events]),
        )

    def _evaluate_runtime(
        self,
        cases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not cases:
            raise ValueError("runtime_cases must not be empty")
        runtime_root = (
            self._project_root
            / ".runtime"
            / "evaluation"
            / uuid.uuid4().hex
        )
        observations = [
            self._execute_runtime_case(case, runtime_root / str(index))
            for index, case in enumerate(cases, start=1)
        ]
        denominators = {
            "tool_call_validity": sum(
                item["proposed_calls"] for item in observations
            ),
            "guardrail_precision": sum(
                item["guardrail_denial_count"] for item in observations
            ),
            "resume_success_rate": sum(
                int(bool(case["resume_case"])) for case in cases
            ),
            "duplicate_side_effect_rate": sum(
                int(bool(case["deterministic_side_effect_case"]))
                for case in cases
            ),
            "hitl_gate_recall": sum(
                int(bool(case["approval_required"])) for case in cases
            ),
            "loop_termination_rate": sum(
                int(bool(case["termination_expected"])) for case in cases
            ),
            "average_tool_calls": len(cases),
            "average_retries": len(cases),
        }
        zero = [name for name, value in denominators.items() if value <= 0]
        if zero:
            raise ValueError(
                f"runtime metric denominators must be non-zero: {sorted(zero)}"
            )
        metrics = {
            "tool_call_validity": sum(
                item["valid_calls"] for item in observations
            )
            / denominators["tool_call_validity"],
            "guardrail_precision": sum(
                item["correct_guardrail_denials"] for item in observations
            )
            / denominators["guardrail_precision"],
            "resume_success_rate": sum(
                int(bool(item["resume_equivalent"]))
                for case, item in zip(cases, observations, strict=True)
                if case["resume_case"]
            )
            / denominators["resume_success_rate"],
            "duplicate_side_effect_rate": sum(
                item["duplicate_side_effects"]
                for case, item in zip(cases, observations, strict=True)
                if case["deterministic_side_effect_case"]
            )
            / denominators["duplicate_side_effect_rate"],
            "hitl_gate_recall": sum(
                int(bool(item["hitl_gate_observed"]))
                for case, item in zip(cases, observations, strict=True)
                if case["approval_required"]
            )
            / denominators["hitl_gate_recall"],
            "loop_termination_rate": sum(
                int(bool(item["terminated_within_budget"]))
                for case, item in zip(cases, observations, strict=True)
                if case["termination_expected"]
            )
            / denominators["loop_termination_rate"],
            "average_tool_calls": sum(
                item["tool_calls"] for item in observations
            )
            / denominators["average_tool_calls"],
            "average_retries": sum(
                item["retries"] for item in observations
            )
            / denominators["average_retries"],
        }
        threshold_results = {
            name: (
                metrics[name] <= threshold
                if name == "duplicate_side_effect_rate"
                else metrics[name] >= threshold
            )
            for name, threshold in _RUNTIME_THRESHOLDS.items()
        }
        return {
            "metrics": metrics,
            "denominators": denominators,
            "thresholds": dict(_RUNTIME_THRESHOLDS),
            "threshold_results": threshold_results,
            "limitations": list(_RUNTIME_LIMITATIONS),
            "cases": observations,
        }

    def _execute_runtime_case(
        self,
        case: Mapping[str, Any],
        root: Path,
    ) -> dict[str, Any]:
        scenario = str(case["scenario"])
        if scenario == "malformed":
            store, run_id, handler_calls = self._run_malformed_case(root, case)
            resume_equivalent = False
        elif scenario == "multi_guardrail_denial":
            store, run_id, handler_calls = self._run_multi_guardrail_case(
                root, case
            )
            resume_equivalent = False
        elif scenario == "crash":
            store, run_id, handler_calls, resume_equivalent = (
                self._run_crash_case(root, case)
            )
        else:
            store, run_id, handler_calls = self._run_loop_case(root, case)
            resume_equivalent = False
        try:
            state = store.load_state(run_id)
            events = store.events(run_id)
            replay = self.replay_runtime_run(store, run_id)
            observation = _runtime_observation(
                case,
                state,
                events,
                replay,
                handler_calls=handler_calls,
                resume_equivalent=resume_equivalent,
            )
        finally:
            store.close()
        return observation

    def _run_loop_case(
        self,
        root: Path,
        case: Mapping[str, Any],
    ) -> tuple[AgentRunStore, str, int]:
        scenario = str(case["scenario"])
        project_path = self._project_root / str(case["project"])
        behavior = {
            "retry": "fail_once",
            "budget_exhaustion": "always_fail",
        }.get(scenario, "complete")
        approval_scope = "approved_change" if scenario == "stale_approval" else None
        allowed_roots = (
            ()
            if scenario == "safe_guardrail_false_positive"
            else None
        )
        registry, calls = self._runtime_registry(
            behavior=behavior,
            approval_scope=approval_scope,
            allowed_roots=allowed_roots,
        )
        budget = (
            RunBudget(max_steps=2, max_tool_calls=2, max_retries=1)
            if scenario == "retry"
            else RunBudget(max_steps=1, max_tool_calls=2, max_retries=1)
            if scenario == "budget_exhaustion"
            else RunBudget(max_steps=1, max_tool_calls=1, max_retries=0)
        )
        store = AgentRunStore(root)
        controller = LoopController(
            store,
            registry,
            ReleaseAgentWorkflowService(),
            ExecutionTracer(run_id=f"eval-{case['name']}"),
        )
        result = controller.run(
            LoopRequest(
                project_path=project_path,
                task_kind="REVIEW",
                budget=budget,
            )
        )
        if scenario == "stale_approval":
            pending_approval_id = result.state.pending_approval_id
            result = controller.resume(
                result.run_id,
                approval={
                    "approval_id": f"stale-{pending_approval_id}",
                    "approved_scopes": ["approved_change"],
                },
            )
        return store, result.run_id, calls["count"]

    def _run_crash_case(
        self,
        root: Path,
        case: Mapping[str, Any],
    ) -> tuple[AgentRunStore, str, int, bool]:
        project_path = self._project_root / str(case["project"])
        control_store = AgentRunStore(root / "control")
        control_registry, _ = self._runtime_registry()
        control = LoopController(
            control_store,
            control_registry,
            ReleaseAgentWorkflowService(),
            None,
        ).run(
            LoopRequest(
                project_path=project_path,
                task_kind="REVIEW",
                budget=RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
            )
        )
        control_replay = self.replay_runtime_run(control_store, control.run_id)
        control_store.close()

        store = AgentRunStore(root / "crashed")
        registry, first_calls = self._runtime_registry()
        controller = _CrashAfterToolCompletedController(
            store,
            registry,
            ReleaseAgentWorkflowService(),
            ExecutionTracer(run_id=f"eval-{case['name']}-crash"),
        )
        try:
            controller.run(
                LoopRequest(
                    project_path=project_path,
                    task_kind="REVIEW",
                    budget=RunBudget(
                        max_steps=1,
                        max_tool_calls=1,
                        max_retries=0,
                    ),
                )
            )
        except _InjectedEvaluationCrash:
            pass
        run_id = controller.last_run_id
        store.close()

        reopened = AgentRunStore(root / "crashed")
        resumed_registry, resumed_calls = self._runtime_registry()
        resumed = LoopController(
            reopened,
            resumed_registry,
            ReleaseAgentWorkflowService(),
            ExecutionTracer(run_id=f"eval-{case['name']}-resume"),
        ).resume(run_id)
        replay = self.replay_runtime_run(reopened, run_id)
        equivalent = (
            resumed.status == control.status
            and replay.decision_digest == control_replay.decision_digest
            and replay.route_history == control_replay.route_history
            and _outcome_values(replay) == _outcome_values(control_replay)
        )
        return (
            reopened,
            run_id,
            first_calls["count"] + resumed_calls["count"],
            equivalent,
        )

    def _run_malformed_case(
        self,
        root: Path,
        case: Mapping[str, Any],
    ) -> tuple[AgentRunStore, str, int]:
        store = AgentRunStore(root)
        run_id = uuid.uuid4().hex
        project_path = self._project_root / str(case["project"])
        budget = RunBudget(max_steps=1, max_tool_calls=1, max_retries=0)
        state = AgentRunState.created(
            run_id=run_id,
            task_kind="REVIEW",
            project_root=project_path,
            budget=budget,
        )
        store.create_run(state)
        state = store.load_state(run_id)
        state = _append_state(
            store,
            state,
            "PLAN_PROPOSED",
            {"plan_digest": sha256_json({"scenario": "malformed"})},
        )
        call = ToolCall.create(
            tool_name="scan_project",
            tool_version="1",
            args={"project_path": str(project_path)},
            run_id=run_id,
            step_index=0,
            idempotency_key=f"{run_id}:0:scan_project",
        )
        state = _append_state(
            store,
            state,
            "TOOL_REQUESTED",
            {
                "tool_name": call.tool_name,
                "tool_version": call.tool_version,
                "canonical_args": call.canonical_args,
                "args_sha256": call.args_sha256,
                "step_index": call.step_index,
                "idempotency_key": call.idempotency_key,
                "requires_approval": False,
            },
        )
        registry, calls = self._runtime_registry()
        result = registry.execute(
            call,
            ToolExecutionContext(budget=budget),
        )
        state = _append_state(
            store,
            state,
            "TOOL_FAILED",
            {
                "idempotency_key": call.idempotency_key,
                "retryable": False,
                "error_type": result.error_type or "malformed_arguments",
                "failure_phase": "guardrail_denied",
            },
        )
        decision_digest = sha256_json(
            {"control": "fail", "error_type": result.error_type}
        )
        state = _append_state(
            store,
            state,
            "EVALUATION_RECORDED",
            {
                "decision_digest": decision_digest,
                "stop_reason": result.error_type or "malformed_arguments",
                "terminal_status": "FAILED",
            },
        )
        state = _append_state(
            store,
            state,
            "CHECKPOINT_COMMITTED",
            {
                "checkpoint_event_id": f"checkpoint:{run_id}:1",
                "step_index": 1,
            },
        )
        _append_state(
            store,
            state,
            "RUN_FAILED",
            {
                "decision_digest": decision_digest,
                "error_type": result.error_type or "malformed_arguments",
            },
        )
        return store, run_id, calls["count"]

    def _run_multi_guardrail_case(
        self,
        root: Path,
        case: Mapping[str, Any],
    ) -> tuple[AgentRunStore, str, int]:
        store = AgentRunStore(root)
        run_id = uuid.uuid4().hex
        project_path = self._project_root / str(case["project"])
        budget = RunBudget(max_steps=2, max_tool_calls=2, max_retries=0)
        state = AgentRunState.created(
            run_id=run_id,
            task_kind="REVIEW",
            project_root=project_path,
            budget=budget,
        )
        store.create_run(state)
        state = _append_state(
            store,
            store.load_state(run_id),
            "PLAN_PROPOSED",
            {"plan_digest": sha256_json({"scenario": case["scenario"]})},
        )
        registry, calls = self._runtime_registry(allowed_roots=())
        context = ToolExecutionContext(budget=budget)
        for index in range(2):
            call = ToolCall.create(
                tool_name="scan_project",
                tool_version="1",
                args={
                    "project_path": str(project_path),
                    "include_pytest_execution": False,
                },
                run_id=run_id,
                step_index=state.step_index,
                idempotency_key=f"{run_id}:{state.step_index}:denied:{index}",
            )
            state = _append_state(
                store,
                state,
                "TOOL_REQUESTED",
                {
                    "tool_name": call.tool_name,
                    "tool_version": call.tool_version,
                    "canonical_args": call.canonical_args,
                    "args_sha256": call.args_sha256,
                    "step_index": call.step_index,
                    "idempotency_key": call.idempotency_key,
                    "requires_approval": False,
                },
            )
            result = registry.execute(call, context)
            if result.status != "blocked" or result.error_type is None:
                raise RuntimeError(
                    "multi-denial fixture did not produce a guardrail denial"
                )
            state = _append_state(
                store,
                state,
                "TOOL_FAILED",
                {
                    "idempotency_key": call.idempotency_key,
                    "retryable": False,
                    "error_type": result.error_type,
                    "failure_phase": "guardrail_denied",
                },
            )
            if index == 0:
                continue_digest = sha256_json(
                    {
                        "control": "continue",
                        "error_type": result.error_type,
                        "case": "multi_guardrail_denial",
                    }
                )
                state = _append_state(
                    store,
                    state,
                    "EVALUATION_RECORDED",
                    {
                        "decision_digest": continue_digest,
                        "stop_reason": "evaluate_next_denial",
                        "terminal_status": "CONTINUE",
                    },
                )
                state = _append_state(
                    store,
                    state,
                    "CHECKPOINT_COMMITTED",
                    {
                        "checkpoint_event_id": f"checkpoint:{run_id}:1",
                        "step_index": 1,
                    },
                )
        decision_digest = sha256_json(
            {"control": "fail", "error_type": "path_not_allowed"}
        )
        state = _append_state(
            store,
            state,
            "EVALUATION_RECORDED",
            {
                "decision_digest": decision_digest,
                "stop_reason": "path_not_allowed",
                "terminal_status": "FAILED",
            },
        )
        state = _append_state(
            store,
            state,
            "CHECKPOINT_COMMITTED",
            {
                "checkpoint_event_id": f"checkpoint:{run_id}:2",
                "step_index": 2,
            },
        )
        _append_state(
            store,
            state,
            "RUN_FAILED",
            {
                "decision_digest": decision_digest,
                "error_type": "path_not_allowed",
            },
        )
        return store, run_id, calls["count"]

    def _runtime_registry(
        self,
        *,
        behavior: str = "complete",
        approval_scope: str | None = None,
        allowed_roots: tuple[Path, ...] | None = None,
    ) -> tuple[ToolRegistry, dict[str, int]]:
        calls = {"count": 0}

        def scan(
            args: dict[str, Any],
            context: ToolExecutionContext,
        ) -> dict[str, Any]:
            calls["count"] += 1
            if behavior == "always_fail" or (
                behavior == "fail_once" and calls["count"] == 1
            ):
                raise ConnectionError("deterministic transient evaluation failure")
            review = self._review.review(
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
                allowed_roots=(
                    (self._project_root,)
                    if allowed_roots is None
                    else allowed_roots
                ),
                network_policy="offline",
                timeout_ms=60_000,
                max_retries=1,
                budget_cost=1,
                required_approval_scope=approval_scope,
            ),
            scan,
        )
        return registry, calls

    def _evaluate_retrieval(
        self, cases: list[dict[str, Any]]
    ) -> dict[str, Any]:
        service = RuleRetrievalService(
            get_default_rule_index_path(),
            embed_model=DeterministicEvaluationEmbedding(),
        )
        expected_total = 0
        hits = 0
        source_hits = 0
        details = []
        for case in cases:
            result = service.retrieve(
                case["query"], mode=case["mode"], top_k=case["top_k"]
            )
            expected = set(case["expected_rule_ids"])
            returned = {item.rule_id for item in result.evidence}
            matched = expected.intersection(returned)
            expected_total += len(expected)
            hits += len(matched)
            source_hits += sum(
                1
                for rule_id in matched
                if any(
                    item.rule_id == rule_id
                    and item.source_url
                    and item.local_source
                    for item in result.evidence
                )
            )
            details.append(
                {
                    "name": case["name"],
                    "mode": result.mode_used,
                    "expected": sorted(expected),
                    "returned": sorted(returned),
                    "matched": sorted(matched),
                }
            )
        denominator = expected_total or 1
        return {
            "recall_at_k": hits / denominator,
            "source_accuracy": source_hits / denominator,
            "cases": details,
        }

    def _evaluate_decisions(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        correct = 0
        details = []
        for case in cases:
            review = self._review.review(
                project_path=self._project_root / case["project"],
                include_pytest_execution=case["include_pytest_execution"],
            )
            matched = review.release_allowed == case["release_allowed"]
            correct += int(matched)
            details.append(
                {
                    "name": case["name"],
                    "expected": case["release_allowed"],
                    "actual": review.release_allowed,
                    "matched": matched,
                }
            )
        return {"accuracy": correct / (len(cases) or 1), "cases": details}

    def _evaluate_paths(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        expected_nodes: set[str] = set()
        observed_nodes: set[str] = set()
        details = []
        service = ReleaseAgentWorkflowService()
        for case in cases:
            result = service.run(
                project_path=self._project_root / case["project"],
                include_pytest_execution=case["include_pytest_execution"],
            )
            expected = set(case["expected_nodes"])
            observed = set(result.state["route_history"])
            expected_nodes.update(expected)
            observed_nodes.update(observed)
            details.append(
                {
                    "name": case["name"],
                    "expected": sorted(expected),
                    "observed": sorted(observed),
                    "covered": expected.issubset(observed),
                }
            )
        return {
            "coverage": len(expected_nodes.intersection(observed_nodes))
            / (len(expected_nodes) or 1),
            "cases": details,
        }

    def _evaluate_llm(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        valid = 0
        details = []
        for case in cases:
            review = self._review.review(
                project_path=self._project_root / case["project"],
                include_pytest_execution=False,
            )
            evidence = review.retrieval_evidence
            advice = build_agent_advice_result(
                project_path=review.project_path,
                results=review.check_results,
            )
            evidence_id = evidence[0].evidence_id
            response = json.dumps(
                {
                    "risk_level": "high",
                    "summary": "Offline golden response.",
                    "release_status": "blocked",
                    "release_allowed": False,
                    "prioritized_risks": [],
                    "fix_plan": [],
                    "evidence_rule_ids": [evidence[0].rule_id],
                    "evidence_ids": [evidence_id],
                    "unsupported_claims": [],
                    "missing_evidence_notes": [],
                }
            )
            result = ReleaseRiskAnalysisAgent(
                llm_client=FakeLLMClient([response])
            ).analyze(
                ReleaseRiskAnalysisContext(
                    advice_result=advice,
                    retrieval_evidence=evidence,
                )
            )
            matched = result.analysis.evidence_ids == (evidence_id,)
            valid += int(matched)
            details.append({"name": case["name"], "valid": matched})
        return {"valid_rate": valid / (len(cases) or 1), "cases": details}

    def _evaluate_verification(
        self, cases: list[dict[str, Any]]
    ) -> dict[str, Any]:
        correct = 0
        details = []
        service = ReleaseVerificationService()
        for case in cases:
            result = service.verify(
                before_project_path=self._project_root / case["before_project"],
                after_project_path=self._project_root / case["after_project"],
                include_pytest_execution=False,
            )
            resolved_rules = {
                item.split("::", 1)[0] for item in result.delta.resolved
            }
            expected = set(case["resolved_rule_ids"])
            matched = expected == resolved_rules and (
                result.release_allowed == case["release_allowed"]
            )
            correct += int(matched)
            details.append(
                {
                    "name": case["name"],
                    "expected_resolved": sorted(expected),
                    "actual_resolved": sorted(resolved_rules),
                    "matched": matched,
                }
            )
        return {"accuracy": correct / (len(cases) or 1), "cases": details}


class _InjectedEvaluationCrash(RuntimeError):
    pass


class _CrashAfterToolCompletedController(LoopController):
    """Exercise the controller's documented post-append crash seam."""

    def _after_event(self, event: RunEvent) -> None:
        if event.event_kind == "TOOL_COMPLETED":
            raise _InjectedEvaluationCrash


def _append_state(
    store: AgentRunStore,
    state: AgentRunState,
    event_kind: str,
    payload: Mapping[str, object],
) -> AgentRunState:
    store.append(
        state.run_id,
        state.last_event_sequence,
        event_kind,
        payload,
    )
    return store.load_state(state.run_id)


def _outcome_values(
    replay: RuntimeReplayResult,
) -> tuple[tuple[str, str | None], ...]:
    return tuple(
        (status, digest_or_error)
        for _, status, digest_or_error in replay.idempotency_outcomes
    )


def _runtime_observation(
    case: Mapping[str, Any],
    state: AgentRunState,
    events: tuple[RunEvent, ...],
    replay: RuntimeReplayResult,
    *,
    handler_calls: int,
    resume_equivalent: bool,
) -> dict[str, Any]:
    event_kinds = [event.event_kind for event in events]
    expected_events = [str(item) for item in case["expected_events"]]
    requested = [event for event in events if event.event_kind == "TOOL_REQUESTED"]
    failed = [event for event in events if event.event_kind == "TOOL_FAILED"]
    started = [event for event in events if event.event_kind == "TOOL_STARTED"]
    guardrail_denials = [
        event
        for event in failed
        if event.payload.get("failure_phase") == "guardrail_denied"
    ]
    raw_labels = case.get("unsafe_call_labels")
    unsafe_labels = (
        [bool(label) for label in raw_labels]
        if isinstance(raw_labels, (list, tuple))
        else [bool(case["unsafe_call"])] * len(guardrail_denials)
    )
    if len(unsafe_labels) != len(guardrail_denials):
        raise ValueError(
            "unsafe_call_labels must match guardrail denial occurrences"
        )
    raw_expected_errors = case.get("expected_guardrail_error_types", ())
    if not isinstance(raw_expected_errors, (list, tuple)):
        raise ValueError("expected_guardrail_error_types must be a list")
    expected_errors = [
        str(value) if value is not None else None
        for value in raw_expected_errors
    ]
    if len(expected_errors) != len(guardrail_denials):
        raise ValueError(
            "expected_guardrail_error_types must match guardrail denials"
        )
    denial_evidence = [
        {
            "event_sequence": denial.sequence,
            "actual_error_type": denial.payload.get("error_type"),
            "expected_error_type": expected,
            "unsafe_label": label,
            "reason_matches": (
                label
                and expected is not None
                and denial.payload.get("error_type") == expected
            ),
        }
        for denial, label, expected in zip(
            guardrail_denials,
            unsafe_labels,
            expected_errors,
            strict=True,
        )
    ]
    unsafe_denial = bool(guardrail_denials)
    correct_guardrail_denials = sum(
        int(bool(item["reason_matches"])) for item in denial_evidence
    )
    requires_approval = any(
        event.payload.get("requires_approval") is True for event in requested
    )
    hitl_gate_observed = requires_approval and state.status == "WAITING_HITL"
    started_keys = {
        str(event.payload["idempotency_key"])
        for event in started
    }
    approval_gated_keys = {
        str(event.payload["idempotency_key"])
        for event in requested
        if event.payload.get("requires_approval") is True
        and state.status == "WAITING_HITL"
    }
    valid_keys = started_keys | approval_gated_keys
    valid_calls = sum(
        int(str(event.payload["idempotency_key"]) in valid_keys)
        for event in requested
    )
    duplicate_side_effects = max(handler_calls - len(started_keys), 0)
    terminal_event_observed = any(
        event.event_kind in {"RUN_COMPLETED", "RUN_FAILED"}
        for event in events
    )
    terminated_within_budget = (
        terminal_event_observed
        and state.status in {"COMPLETED", "FAILED"}
        and state.step_index <= state.budget.max_steps
        and state.tool_call_count <= state.budget.max_tool_calls
        and state.retry_count <= state.budget.max_retries
    )
    expected_status = str(case["expected_final_status"])
    matched = _is_ordered_subsequence(expected_events, event_kinds) and (
        state.status == expected_status
    )
    if case["unsafe_call"]:
        matched = matched and any(
            bool(item["reason_matches"]) for item in denial_evidence
        )
    if case["approval_required"]:
        matched = matched and hitl_gate_observed
    if case["resume_case"]:
        matched = matched and resume_equivalent
    if case["termination_expected"]:
        matched = matched and terminated_within_budget
    if case["deterministic_side_effect_case"]:
        matched = matched and duplicate_side_effects == 0
    all_sequences = [event.sequence for event in events]
    contributions = {
        "tool_call_validity": {
            "numerator": valid_calls,
            "denominator": len(requested),
        },
        "guardrail_precision": {
            "numerator": correct_guardrail_denials,
            "denominator": len(guardrail_denials),
        },
        "resume_success_rate": {
            "numerator": int(resume_equivalent),
            "denominator": int(bool(case["resume_case"])),
        },
        "duplicate_side_effect_rate": {
            "numerator": duplicate_side_effects,
            "denominator": int(bool(case["deterministic_side_effect_case"])),
        },
        "hitl_gate_recall": {
            "numerator": int(hitl_gate_observed),
            "denominator": int(bool(case["approval_required"])),
        },
        "loop_termination_rate": {
            "numerator": int(terminated_within_budget),
            "denominator": int(bool(case["termination_expected"])),
        },
        "average_tool_calls": {
            "numerator": state.tool_call_count,
            "denominator": 1,
        },
        "average_retries": {
            "numerator": state.retry_count,
            "denominator": 1,
        },
    }
    return {
        "name": str(case["name"]),
        "scenario": str(case["scenario"]),
        "expected_events": expected_events,
        "observed_events": event_kinds,
        "expected_final_status": expected_status,
        "observed_final_status": state.status,
        "decision_digest": replay.decision_digest,
        "route_history": list(replay.route_history),
        "idempotency_outcomes": [
            list(outcome) for outcome in replay.idempotency_outcomes
        ],
        "checkpoint_sequence": replay.checkpoint_sequence,
        "event_digest": replay.event_digest,
        "proposed_calls": len(requested),
        "valid_calls": valid_calls,
        "guardrail_denied": unsafe_denial,
        "guardrail_denial_count": len(guardrail_denials),
        "correct_guardrail_denials": correct_guardrail_denials,
        "guardrail_correct": bool(correct_guardrail_denials),
        "guardrail_denial_evidence": denial_evidence,
        "resume_equivalent": resume_equivalent,
        "duplicate_side_effects": duplicate_side_effects,
        "hitl_gate_observed": hitl_gate_observed,
        "terminated_within_budget": terminated_within_budget,
        "tool_calls": state.tool_call_count,
        "retries": state.retry_count,
        "handler_invocations": handler_calls,
        "metric_evidence": [
            {
                "metric": name,
                "event_sequences": all_sequences,
                **values,
            }
            for name, values in contributions.items()
        ],
        "matched": matched,
    }


def _is_ordered_subsequence(
    expected: list[str],
    observed: list[str],
) -> bool:
    observed_index = 0
    for expected_kind in expected:
        while (
            observed_index < len(observed)
            and observed[observed_index] != expected_kind
        ):
            observed_index += 1
        if observed_index == len(observed):
            return False
        observed_index += 1
    return True
