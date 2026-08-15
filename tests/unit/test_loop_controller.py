import hashlib
import json
import sqlite3
from dataclasses import replace
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
from releaseguard_agent.models.project_memory import (
    MemoryKind,
    MemoryProvenance,
    MemoryQueryBudget,
    MemoryStatus,
    ProjectMemoryRecord,
)
from releaseguard_agent.models.relation_index import RelationQueryBudget
from releaseguard_agent.rag import (
    RetrievalResult,
    RuleRetrievalService,
    get_default_rule_index_path,
)
from releaseguard_agent.rag.project_memory import ProjectMemoryStore
from releaseguard_agent.rag.relation_index import RelationIndexBuilder
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


def test_versioned_artifact_context_is_fingerprinted_traced_and_never_persists_raw(
    tmp_path: Path,
) -> None:
    """Removing the pre-start envelope or durable sanitization leaks support data."""

    artifact_root = PROJECT_ROOT / ".runtime" / "task4-loop" / hashlib.sha256(
        str(tmp_path).encode("utf-8")
    ).hexdigest()[:16]
    relation_root = artifact_root / "relations"
    memory_root = artifact_root / "memory"
    relation = RelationIndexBuilder().build(
        get_default_rule_index_path(), relation_root
    )
    raw_memory = "TASK4-RAW-MEMORY-CONTEXT must never enter durable storage"
    memory = ProjectMemoryStore(memory_root).publish(
        "project-alpha", (_runtime_memory_record(raw_memory),)
    )
    evidence = EvidenceSearchTool(
        RuleRetrievalService(
            get_default_rule_index_path(),
            relation_snapshot_root=relation_root,
        ),
        relation_snapshot_root=relation_root,
        memory_root=memory_root,
    )
    controller, store = _controller(tmp_path, _tools(evidence=evidence))

    result = controller.run(
        LoopRequest(
            project_path=SAMPLES / "fastapi_bad_project",
            task_kind="REVIEW",
            budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=0),
            retrieval_mode="graph_hybrid",
            relation_index_version=relation.manifest.index_version,
            memory_project_id="project-alpha",
            memory_version=memory.manifest.memory_version,
            relation_budget=RelationQueryBudget(2, 500, 500, 8_000),
            memory_budget=MemoryQueryBudget(1, 200, 30),
        )
    )

    assert result.status == "COMPLETED"
    assert result.review is not None and result.review.release_allowed is False
    events = store.events(result.run_id)
    requested = next(
        event for event in events
        if event.event_kind == "TOOL_REQUESTED"
        and event.payload["tool_name"] == "search_rule_evidence"
    )
    args = json.loads(str(requested.payload["canonical_args"]))
    artifact_context = args["artifact_context"]
    assert artifact_context["relation_index_version"] == relation.manifest.index_version
    assert artifact_context["memory_version"] == memory.manifest.memory_version
    assert artifact_context["selected_memory_ids"] == ["memory-runtime"]
    assert args["memory_budget"] == {
        "max_characters": 200,
        "max_context_units": 30,
        "top_k": 1,
    }
    planned = next(event for event in events if event.event_kind == "PLAN_PROPOSED")
    assert dict(planned.payload["memory_budget"]) == {
        "max_characters": 200,
        "max_context_units": 30,
        "top_k": 1,
    }
    evidence_started = next(
        event for event in events
        if event.event_kind == "TOOL_STARTED"
        and event.payload["idempotency_key"] == requested.payload["idempotency_key"]
    )
    assert requested.sequence < evidence_started.sequence
    with sqlite3.connect(tmp_path / "runtime" / "agent_runs.sqlite3") as connection:
        durable = repr([
            *connection.execute("SELECT * FROM run_events").fetchall(),
            *connection.execute("SELECT * FROM run_snapshots").fetchall(),
            *connection.execute("SELECT * FROM tool_results").fetchall(),
        ])
    assert raw_memory not in durable
    assert result.trace_path is not None
    trace = json.loads(result.trace_path.read_text(encoding="utf-8"))
    trace_text = repr(trace)
    assert raw_memory not in trace_text
    traced_contexts = [
        event["artifact_context"]
        for event in trace["events"]
        if event.get("artifact_context")
    ]
    assert traced_contexts
    assert any(context["selected_memory_ids"] == ["memory-runtime"] for context in traced_contexts)
    assert any(context["relation_path_ids"] for context in traced_contexts)
    store.close()


def test_missing_artifacts_fallback_before_start_and_corruption_pauses_closed(
    tmp_path: Path,
) -> None:
    """Missing support is optional; tampering must never reach the evidence role."""

    artifact_root = PROJECT_ROOT / ".runtime" / "task4-failures" / hashlib.sha256(
        str(tmp_path).encode("utf-8")
    ).hexdigest()[:16]
    relation_root = artifact_root / "relations"
    memory_root = artifact_root / "memory"
    relation = RelationIndexBuilder().build(
        get_default_rule_index_path(), relation_root
    )
    memory = ProjectMemoryStore(memory_root).publish(
        "project-alpha", (_runtime_memory_record("safe task4 memory"),)
    )

    class CountingEvidence(EvidenceSearchTool):
        def __init__(self) -> None:
            super().__init__(
                RuleRetrievalService(
                    get_default_rule_index_path(),
                    relation_snapshot_root=relation_root,
                ),
                relation_snapshot_root=relation_root,
                memory_root=memory_root,
            )
            self.calls = 0

        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            return super().invoke(*args, **kwargs)

    missing_tool = CountingEvidence()
    missing_controller, missing_store = _controller(
        tmp_path / "missing", _tools(evidence=missing_tool)
    )
    missing = missing_controller.run(
        LoopRequest(
            project_path=SAMPLES / "fastapi_bad_project",
            task_kind="REVIEW",
            budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=0),
            retrieval_mode="graph_hybrid",
            relation_index_version="ri-" + "0" * 64,
            memory_project_id="project-alpha",
            memory_version="pm-" + "0" * 64,
            relation_budget=RelationQueryBudget(2, 24, 24, 8_000),
            memory_budget=MemoryQueryBudget(1, 200, 30),
        )
    )
    assert missing.status == "COMPLETED"
    assert missing.review is not None and missing.review.release_allowed is False
    assert missing.status != "WAITING_HITL"
    missing_request = next(
        event for event in missing_store.events(missing.run_id)
        if event.event_kind == "TOOL_REQUESTED"
        and event.payload["tool_name"] == "search_rule_evidence"
    )
    missing_args = json.loads(str(missing_request.payload["canonical_args"]))
    assert missing_args["artifact_context"]["relation_fallback_reason"] == "relation_snapshot_missing"
    assert missing_args["artifact_context"]["memory_fallback_reason"] == "memory_snapshot_missing"
    assert missing_tool.calls >= 1
    assert not any(
        event.event_kind == "APPROVAL_REQUESTED"
        for event in missing_store.events(missing.run_id)
    )
    plain_controller, plain_store = _controller(tmp_path / "plain", _tools())
    plain = plain_controller.run(
        LoopRequest(
            project_path=SAMPLES / "fastapi_bad_project",
            task_kind="REVIEW",
            budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=0),
        )
    )
    assert plain.review is not None
    assert plain.review.release_allowed == missing.review.release_allowed
    assert plain.review.check_results == missing.review.check_results
    assert not any(
        event.event_kind == "APPROVAL_REQUESTED"
        for event in plain_store.events(plain.run_id)
    )
    plain_store.close()
    missing_store.close()

    manifest = relation_root / relation.manifest.index_version / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    corrupt_tool = CountingEvidence()
    corrupt_controller, corrupt_store = _controller(
        tmp_path / "corrupt", _tools(evidence=corrupt_tool)
    )
    corrupt = corrupt_controller.run(
        LoopRequest(
            project_path=SAMPLES / "fastapi_bad_project",
            task_kind="REVIEW",
            budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=3),
            retrieval_mode="graph_hybrid",
            relation_index_version=relation.manifest.index_version,
            memory_project_id="project-alpha",
            memory_version=memory.manifest.memory_version,
            relation_budget=RelationQueryBudget(2, 24, 24, 8_000),
            memory_budget=MemoryQueryBudget(1, 200, 30),
        )
    )
    assert corrupt.status == "PAUSED"
    assert corrupt.metrics["stop_reason"] == "artifact_integrity_failure"
    assert corrupt_tool.calls == 0
    corrupt_events = corrupt_store.events(corrupt.run_id)
    corrupt_request = next(
        event for event in corrupt_events
        if event.event_kind == "TOOL_REQUESTED"
        and event.payload["tool_name"] == "search_rule_evidence"
    )
    assert not any(
        event.event_kind == "TOOL_STARTED"
        and event.payload["idempotency_key"] == corrupt_request.payload["idempotency_key"]
        for event in corrupt_events
    )
    assert not any(event.event_kind == "TOOL_FAILED" for event in corrupt_events)
    corrupt_store.close()


def test_missing_internal_artifact_or_parent_pauses_before_evidence_handler(
    tmp_path: Path,
) -> None:
    """Only an absent requested top-level version is an optional fallback."""

    artifact_root = PROJECT_ROOT / ".runtime" / "task4-integrity-boundary" / hashlib.sha256(
        str(tmp_path).encode("utf-8")
    ).hexdigest()[:16]
    relation_root = artifact_root / "relations"
    memory_root = artifact_root / "memory"
    relation = RelationIndexBuilder().build(
        get_default_rule_index_path(), relation_root
    )
    memory_store = ProjectMemoryStore(memory_root)
    parent_memory = memory_store.publish(
        "project-alpha", (_runtime_memory_record("safe parent memory"),)
    )
    child_memory = memory_store.publish(
        "project-alpha",
        (replace(parent_memory.records[0], memory_version="pm-pending"),),
        parent_memory_version=parent_memory.manifest.memory_version,
    )

    class CountingEvidence(EvidenceSearchTool):
        def __init__(self) -> None:
            super().__init__(
                RuleRetrievalService(
                    get_default_rule_index_path(),
                    relation_snapshot_root=relation_root,
                ),
                relation_snapshot_root=relation_root,
                memory_root=memory_root,
            )
            self.calls = 0

        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            return super().invoke(*args, **kwargs)

    nodes_path = relation_root / relation.manifest.index_version / "nodes.json"
    hidden_nodes = nodes_path.with_name("nodes.hidden")
    nodes_path.rename(hidden_nodes)
    missing_child_tool = CountingEvidence()
    missing_child_controller, missing_child_store = _controller(
        tmp_path / "missing-child", _tools(evidence=missing_child_tool)
    )
    missing_child = missing_child_controller.run(
        LoopRequest(
            project_path=SAMPLES / "fastapi_bad_project",
            task_kind="REVIEW",
            budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=0),
            retrieval_mode="graph_hybrid",
            relation_index_version=relation.manifest.index_version,
        )
    )
    assert missing_child.status == "PAUSED"
    assert missing_child.metrics["stop_reason"] == "artifact_integrity_failure"
    assert missing_child_tool.calls == 0
    missing_child_store.close()
    hidden_nodes.rename(nodes_path)

    parent_path = memory_root / parent_memory.manifest.memory_version
    hidden_parent = memory_root / f"hidden-{parent_memory.manifest.memory_version}"
    parent_path.rename(hidden_parent)
    missing_parent_tool = CountingEvidence()
    missing_parent_controller, missing_parent_store = _controller(
        tmp_path / "missing-parent", _tools(evidence=missing_parent_tool)
    )
    missing_parent = missing_parent_controller.run(
        LoopRequest(
            project_path=SAMPLES / "fastapi_bad_project",
            task_kind="REVIEW",
            budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=0),
            memory_project_id="project-alpha",
            memory_version=child_memory.manifest.memory_version,
        )
    )
    assert missing_parent.status == "PAUSED"
    assert missing_parent.metrics["stop_reason"] == "artifact_integrity_failure"
    assert missing_parent_tool.calls == 0
    missing_parent_store.close()
    hidden_parent.rename(parent_path)


def test_relation_hop_budget_falls_back_before_tool_start(
    tmp_path: Path,
) -> None:
    """Unsupported graph depth is a deterministic text fallback, not failure."""

    relation_root = (
        PROJECT_ROOT
        / ".runtime"
        / "task4-hop-budget"
        / hashlib.sha256(str(tmp_path).encode("utf-8")).hexdigest()[:16]
    )
    relation = RelationIndexBuilder().build(
        get_default_rule_index_path(), relation_root
    )

    class CountingEvidence(EvidenceSearchTool):
        def __init__(self) -> None:
            super().__init__(
                RuleRetrievalService(
                    get_default_rule_index_path(),
                    relation_snapshot_root=relation_root,
                ),
                relation_snapshot_root=relation_root,
            )
            self.calls = 0

        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            return super().invoke(*args, **kwargs)

    evidence = CountingEvidence()
    controller, store = _controller(
        tmp_path / "hop-budget", _tools(evidence=evidence)
    )
    result = controller.run(
        LoopRequest(
            project_path=SAMPLES / "fastapi_bad_project",
            task_kind="REVIEW",
            budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=0),
            retrieval_mode="graph_hybrid",
            relation_index_version=relation.manifest.index_version,
            relation_budget=RelationQueryBudget(3, 24, 24, 8_000),
        )
    )

    assert result.status == "COMPLETED"
    assert evidence.calls >= 1
    events = store.events(result.run_id)
    requested = next(
        event
        for event in events
        if event.event_kind == "TOOL_REQUESTED"
        and event.payload["tool_name"] == "search_rule_evidence"
    )
    requested_args = json.loads(str(requested.payload["canonical_args"]))
    assert requested_args["relation_budget"]["max_hops"] == 3
    assert requested_args["artifact_context"]["relation_fallback_reason"] == (
        "relation_hop_budget_exceeded"
    )
    started = next(
        event
        for event in events
        if event.event_kind == "TOOL_STARTED"
        and event.payload["idempotency_key"] == requested.payload["idempotency_key"]
    )
    assert requested.sequence < started.sequence
    assert not any(
        event.event_kind == "TOOL_FAILED"
        and event.payload["idempotency_key"] == requested.payload["idempotency_key"]
        for event in events
    )
    store.close()


def _runtime_memory_record(content: str) -> ProjectMemoryRecord:
    return ProjectMemoryRecord(
        memory_id="memory-runtime",
        project_id="project-alpha",
        kind=MemoryKind.CONSTRAINT,
        content=content,
        provenance=MemoryProvenance(
            run_id="run-task4",
            event_id="event-task4",
            evidence_id=None,
            rule_id="RG-DEPS-001",
            human_correction_id=None,
        ),
        created_at_utc="2026-08-15T00:00:00+00:00",
        updated_at_utc="2026-08-15T00:00:00+00:00",
        status=MemoryStatus.ACTIVE,
        confidence=0.9,
        supersedes=None,
        expires_at_utc=None,
        memory_version="pm-pending",
    )
