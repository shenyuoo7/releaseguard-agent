import json
from pathlib import Path

import pytest

from releaseguard_agent.agent_tools import (
    EvidenceSearchTool,
    FixPlanTool,
    ReleaseWorkflowTools,
    RiskAnalysisTool,
    ScanProjectTool,
)
from releaseguard_agent.llm import FakeLLMClient, LLMResponse, LLMRuntime
from releaseguard_agent.agents.role_agents import (
    EvidenceAgentOutput,
    FixPlannerAgentOutput,
    RiskAgentOutput,
)
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


def test_clean_ai_path_runs_evidence_and_real_risk_node() -> None:
    class GroundedClient:
        def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            payload = json.loads(messages[-1].content)
            context = payload["deterministic_context"]
            evidence_ids = [
                item["evidence_id"] for item in context["retrieval_evidence"]
            ]
            return LLMResponse(
                content=json.dumps(
                    {
                        "risk_level": "low",
                        "summary": "项目通过确定性检查，仍建议发布前复核配置。",
                        "release_status": "ready",
                        "release_allowed": True,
                        "prioritized_risks": [],
                        "fix_plan": [],
                        "evidence_rule_ids": [],
                        "evidence_ids": evidence_ids,
                        "unsupported_claims": [],
                        "missing_evidence_notes": [],
                    }
                )
            )

    runtime = LLMRuntime("llm", "fake", "fake-model", GroundedClient())
    result = ReleaseAgentWorkflowService(llm_runtime=runtime).run(
        project_path=SAMPLES / "clean_python_project",
        include_pytest_execution=False,
        force_ai_review=True,
    )

    assert result.release_allowed is True
    assert result.state["llm_attempted"] is True
    assert result.state["llm_failed"] is False
    assert result.state["route_history"] == [
        "scan",
        "evidence_agent",
        "risk_agent",
        "fix_planner_agent",
    ]


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


def test_pure_graph_routes_committed_role_outputs_without_invoking_tools() -> None:
    class ExplodingScan(ScanProjectTool):
        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("pure graph invoked scan")

    class ExplodingEvidence(EvidenceSearchTool):
        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("pure graph invoked evidence")

    class ExplodingRisk(RiskAnalysisTool):
        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("pure graph invoked risk")

    class ExplodingFix(FixPlanTool):
        def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("pure graph invoked fix planner")

    review = ReleaseReviewService().review(
        project_path=SAMPLES / "fastapi_bad_project",
        include_pytest_execution=False,
    )
    evidence = EvidenceAgentOutput(
        evidence=review.retrieval_evidence,
        sufficient=True,
        supplemental_attempted=False,
        manual_review_required=False,
        degraded_reason=None,
    )
    risk = RiskAgentOutput(
        analysis={
            "analysis_source": "deterministic",
            "release_allowed": review.release_allowed,
        },
        evidence_ids=tuple(item.evidence_id for item in evidence.evidence),
        llm_attempted=False,
        llm_failed=False,
        error_type=None,
    )
    fix = FixPlannerAgentOutput(
        steps=(),
        covered_rule_ids=(),
        evidence_ids=(),
    )
    service = ReleaseAgentWorkflowService(
        tools=ReleaseWorkflowTools(
            scan=ExplodingScan(),
            evidence=ExplodingEvidence(
                RuleRetrievalService(get_default_rule_index_path())
            ),
            risk=ExplodingRisk(),
            fix_plan=ExplodingFix(),
        )
    )

    result = service.evaluate_committed(
        review=review,
        evidence_output=evidence,
        risk_output=risk,
        fix_plan_output=fix,
    )

    assert result.state["route_history"] == [
        "scan",
        "evidence_agent",
        "risk_agent",
        "fix_planner_agent",
    ]


def test_pure_graph_fails_closed_when_committed_role_output_is_missing() -> None:
    review = ReleaseReviewService().review(
        project_path=SAMPLES / "fastapi_bad_project",
        include_pytest_execution=False,
    )

    with pytest.raises(ValueError, match="committed evidence"):
        ReleaseAgentWorkflowService().evaluate_committed(review=review)
