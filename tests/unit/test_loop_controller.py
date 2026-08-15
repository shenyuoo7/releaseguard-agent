from pathlib import Path

from releaseguard_agent.agent_tools import (
    EvidenceSearchTool,
    FixPlanTool,
    ReleaseWorkflowTools,
    RiskAnalysisTool,
    ScanProjectTool,
    build_release_tool_registry,
)
from releaseguard_agent.observability import ExecutionTracer
from releaseguard_agent.rag import (
    RetrievalResult,
    RuleRetrievalService,
    get_default_rule_index_path,
)
from releaseguard_agent.runtime.loop import LoopController, LoopRequest
from releaseguard_agent.runtime.models import RunBudget
from releaseguard_agent.runtime.store import AgentRunStore
from releaseguard_agent.services import ReleaseReviewService
from releaseguard_agent.services.agent_workflow_service import (
    ReleaseAgentWorkflowService,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = PROJECT_ROOT / "sample_projects"


def _tools(
    *,
    scan: ScanProjectTool | None = None,
    evidence: EvidenceSearchTool | None = None,
) -> ReleaseWorkflowTools:
    retrieval = RuleRetrievalService(get_default_rule_index_path())
    return ReleaseWorkflowTools(
        scan=scan or ScanProjectTool(ReleaseReviewService()),
        evidence=evidence or EvidenceSearchTool(retrieval),
        risk=RiskAnalysisTool(),
        fix_plan=FixPlanTool(),
    )


def _controller(
    tmp_path: Path,
    tools: ReleaseWorkflowTools,
) -> tuple[LoopController, AgentRunStore]:
    store = AgentRunStore(tmp_path / "runtime")
    controller = LoopController(
        store,
        build_release_tool_registry(tools, allowed_roots=(SAMPLES,)),
        ReleaseAgentWorkflowService(tools=tools),
        ExecutionTracer("task-4-loop-test"),
    )
    return controller, store


def _request(project_name: str, *, budget: RunBudget | None = None) -> LoopRequest:
    return LoopRequest(
        project_path=SAMPLES / project_name,
        task_kind="REVIEW",
        budget=budget or RunBudget(max_steps=1, max_tool_calls=1, max_retries=0),
    )


def test_clean_review_completes_from_deterministic_evaluation(tmp_path: Path) -> None:
    controller, store = _controller(tmp_path, _tools())

    result = controller.run(_request("clean_python_project"))

    assert result.status == "COMPLETED"
    assert result.review is not None and result.review.release_allowed is True
    assert result.review.include_pytest_execution is False
    assert result.route_history == ("scan", "finalize_clean")
    assert result.state.decision_digest
    assert result.metrics["tool_calls"] == 1
    assert [event.event_kind for event in store.events(result.run_id)] == [
        "RUN_CREATED",
        "PLAN_PROPOSED",
        "TOOL_REQUESTED",
        "TOOL_STARTED",
        "TOOL_COMPLETED",
        "EVALUATION_RECORDED",
        "CHECKPOINT_COMMITTED",
        "RUN_COMPLETED",
    ]
    store.close()


def test_blocking_review_preserves_deterministic_release_decision(
    tmp_path: Path,
) -> None:
    controller, store = _controller(tmp_path, _tools())

    result = controller.run(
        _request(
            "fastapi_bad_project",
            budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=0),
        )
    )

    assert result.status == "COMPLETED"
    assert result.review is not None and result.review.release_allowed is False
    assert result.route_history == (
        "scan",
        "evidence_agent",
        "risk_agent",
        "fix_planner_agent",
    )
    events = store.events(result.run_id)
    requested = [
        str(event.payload["tool_name"])
        for event in events
        if event.event_kind == "TOOL_REQUESTED"
    ]
    assert requested == [
        "scan_project",
        "search_rule_evidence",
        "analyze_risk",
        "build_fix_plan",
    ]
    assert [event.event_kind for event in events].count("TOOL_STARTED") == 4
    assert [event.event_kind for event in events].count("TOOL_COMPLETED") == 4
    assert result.metrics["tool_calls"] == 4
    store.close()


def test_evidence_gap_keeps_the_graph_manual_route_observational(
    tmp_path: Path,
) -> None:
    class EmptyEvidenceSearchTool(EvidenceSearchTool):
        def invoke(
            self,
            query: str,
            *,
            mode: str,
            top_k: int,
            tracer=None,
        ) -> RetrievalResult:
            return RetrievalResult(query, mode, mode, None, ())

    retrieval = RuleRetrievalService(get_default_rule_index_path())
    tools = _tools(evidence=EmptyEvidenceSearchTool(retrieval))
    controller, store = _controller(tmp_path, tools)

    result = controller.run(
        _request(
            "fastapi_bad_project",
            budget=RunBudget(max_steps=2, max_tool_calls=2, max_retries=0),
        )
    )

    assert result.status == "COMPLETED"
    assert result.review is not None and result.review.release_allowed is False
    assert result.route_history == ("scan", "evidence_agent", "manual_review")
    assert result.metrics["manual_review_required"] is True
    store.close()


def test_configured_llm_role_is_offline_blocked_before_handler_invocation(
    tmp_path: Path,
) -> None:
    class SentinelNetworkRisk(RiskAnalysisTool):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        @property
        def network_policy(self) -> str:
            return "network"

        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise AssertionError("offline controller invoked configured LLM role")

    risk = SentinelNetworkRisk()
    retrieval = RuleRetrievalService(get_default_rule_index_path())
    tools = ReleaseWorkflowTools(
        scan=ScanProjectTool(ReleaseReviewService()),
        evidence=EvidenceSearchTool(retrieval),
        risk=risk,
        fix_plan=FixPlanTool(),
    )
    controller, store = _controller(tmp_path, tools)

    result = controller.run(
        _request(
            "fastapi_bad_project",
            budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=0),
        )
    )

    assert result.status == "FAILED"
    assert result.metrics["stop_reason"] == "network_disabled"
    assert risk.calls == 0
    risk_request = next(
        event
        for event in store.events(result.run_id)
        if event.event_kind == "TOOL_REQUESTED"
        and event.payload["tool_name"] == "analyze_risk"
    )
    assert not any(
        event.event_kind == "TOOL_STARTED"
        and event.payload["idempotency_key"]
        == risk_request.payload["idempotency_key"]
        for event in store.events(result.run_id)
    )
    store.close()


def test_one_transient_tool_failure_is_retried_within_budgets(
    tmp_path: Path,
) -> None:
    class TransientScanTool(ScanProjectTool):
        def __init__(self) -> None:
            super().__init__(ReleaseReviewService())
            self.calls = 0

        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("injected transient failure")
            return super().invoke(*args, **kwargs)

    scan = TransientScanTool()
    controller, store = _controller(tmp_path, _tools(scan=scan))
    request = _request(
        "clean_python_project",
        budget=RunBudget(max_steps=2, max_tool_calls=2, max_retries=1),
    )

    result = controller.run(request)

    assert result.status == "COMPLETED"
    assert result.state.retry_count == 1
    assert result.state.step_index == 2
    assert result.metrics["tool_calls"] == 2
    assert scan.calls == 2
    assert [event.event_kind for event in store.events(result.run_id)].count(
        "TOOL_FAILED"
    ) == 1
    store.close()


def test_budget_exhaustion_fails_closed_without_an_extra_tool_call(
    tmp_path: Path,
) -> None:
    class AlwaysTransientScanTool(ScanProjectTool):
        def __init__(self) -> None:
            super().__init__(ReleaseReviewService())
            self.calls = 0

        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise TimeoutError("injected transient failure")

    scan = AlwaysTransientScanTool()
    controller, store = _controller(tmp_path, _tools(scan=scan))
    request = _request(
        "clean_python_project",
        budget=RunBudget(max_steps=1, max_tool_calls=2, max_retries=2),
    )

    result = controller.run(request)

    assert result.status == "FAILED"
    assert result.state.step_index == 1
    assert scan.calls == 1
    assert result.metrics["stop_reason"] == "step_budget_exhausted"
    assert store.events(result.run_id)[-1].event_kind == "RUN_FAILED"
    store.close()


def test_tool_retry_limit_is_enforced_below_the_run_retry_budget(
    tmp_path: Path,
) -> None:
    class AlwaysTransientScanTool(ScanProjectTool):
        def __init__(self) -> None:
            super().__init__(ReleaseReviewService())
            self.calls = 0

        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise TimeoutError("injected transient failure")

    scan = AlwaysTransientScanTool()
    controller, store = _controller(tmp_path, _tools(scan=scan))
    request = _request(
        "clean_python_project",
        budget=RunBudget(max_steps=3, max_tool_calls=3, max_retries=2),
    )

    result = controller.run(request)

    assert result.status == "FAILED"
    assert scan.calls == 2
    assert result.state.retry_count == 1
    assert result.metrics["stop_reason"] == "TimeoutError"
    assert [event.event_kind for event in store.events(result.run_id)][-4:] == [
        "TOOL_FAILED",
        "EVALUATION_RECORDED",
        "CHECKPOINT_COMMITTED",
        "RUN_FAILED",
    ]
    assert result.state.decision_digest
    store.close()


def test_resuming_a_terminal_run_does_not_append_events(tmp_path: Path) -> None:
    controller, store = _controller(tmp_path, _tools())
    completed = controller.run(_request("clean_python_project"))
    before = store.events(completed.run_id)

    resumed = controller.resume(completed.run_id)

    assert resumed.status == "COMPLETED"
    assert resumed.state == completed.state
    assert resumed.review == completed.review
    assert store.events(completed.run_id) == before
    store.close()
