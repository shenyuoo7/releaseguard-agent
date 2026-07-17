from pathlib import Path

from releaseguard_agent.agent_tools import (
    EvidenceSearchTool,
    FixPlanTool,
    ReleaseWorkflowTools,
    RiskAnalysisTool,
    ScanProjectTool,
)
from releaseguard_agent.llm import FakeLLMClient, LLMRuntime
from releaseguard_agent.rag import (
    RetrievalResult,
    RuleRetrievalService,
    get_default_rule_index_path,
)
from releaseguard_agent.services import ReleaseReviewService
from releaseguard_agent.services.agent_workflow_service import (
    ReleaseAgentWorkflowService,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = PROJECT_ROOT / "sample_projects"


def test_clean_path_skips_retrieval_risk_and_fix_nodes() -> None:
    result = ReleaseAgentWorkflowService().run(
        project_path=SAMPLES / "clean_python_project",
        include_pytest_execution=False,
    )

    assert result.release_allowed is True
    assert result.state["route_history"] == ["scan", "finalize_clean"]
    assert result.state["fix_plan"] == ()
    assert result.state["llm_attempted"] is False


def test_blocking_path_runs_evidence_risk_and_fix_nodes() -> None:
    result = ReleaseAgentWorkflowService().run(
        project_path=SAMPLES / "fastapi_bad_project",
        include_pytest_execution=False,
    )

    assert result.release_allowed is False
    assert result.state["route_history"] == [
        "scan",
        "evidence_agent",
        "risk_agent",
        "fix_planner_agent",
    ]
    assert result.state["evidence"]
    assert result.state["fix_plan"]
    assert result.state["risk_analysis"]["release_allowed"] is False


def test_insufficient_evidence_routes_to_supplement_and_manual_review() -> None:
    class EmptyEvidenceSearchTool(EvidenceSearchTool):
        def invoke(self, query: str, *, mode: str, top_k: int) -> RetrievalResult:
            return RetrievalResult(query, mode, mode, None, ())

    retrieval = RuleRetrievalService(get_default_rule_index_path())
    tools = ReleaseWorkflowTools(
        scan=ScanProjectTool(ReleaseReviewService()),
        evidence=EmptyEvidenceSearchTool(retrieval),
        risk=RiskAnalysisTool(),
        fix_plan=FixPlanTool(),
    )
    result = ReleaseAgentWorkflowService(tools=tools).run(
        project_path=SAMPLES / "fastapi_bad_project",
        include_pytest_execution=False,
    )

    assert result.state["route_history"] == [
        "scan",
        "evidence_agent",
        "manual_review",
    ]
    assert result.state["supplemental_retrieval"] is True
    assert result.state["manual_review_required"] is True
    assert "risk_analysis" not in result.state


def test_llm_failure_routes_through_deterministic_fallback() -> None:
    runtime = LLMRuntime(
        mode="llm",
        provider="fake",
        model="fake-model",
        client=FakeLLMClient(["not-json"]),
    )
    result = ReleaseAgentWorkflowService(llm_runtime=runtime).run(
        project_path=SAMPLES / "fastapi_bad_project",
        include_pytest_execution=False,
    )

    assert result.state["llm_attempted"] is True
    assert result.state["llm_failed"] is True
    assert result.state["error_type"] == "ReleaseRiskAnalysisParseError"
    assert result.state["risk_analysis"]["analysis_source"] == "deterministic"
    assert result.state["route_history"] == [
        "scan",
        "evidence_agent",
        "risk_agent",
        "deterministic_fallback",
        "fix_planner_agent",
    ]


def test_compiled_graph_exposes_real_nodes_and_edges() -> None:
    graph = ReleaseAgentWorkflowService().graph.get_graph()

    assert {
        "scan",
        "evidence_agent",
        "risk_agent",
        "deterministic_fallback",
        "fix_planner_agent",
        "finalize_clean",
        "verification_complete",
        "verifier_agent",
        "manual_review",
    }.issubset(graph.nodes)
    assert len(graph.edges) >= 9
