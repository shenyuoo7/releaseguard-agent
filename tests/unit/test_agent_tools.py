from pathlib import Path

from releaseguard_agent.agent_tools import (
    EvidenceSearchTool,
    FixPlanTool,
    RiskAnalysisTool,
    ScanProjectTool,
)
from releaseguard_agent.rag import RuleRetrievalService, get_default_rule_index_path
from releaseguard_agent.services import ReleaseReviewService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_scan_and_evidence_tools_call_real_services() -> None:
    review = ScanProjectTool(ReleaseReviewService()).invoke(
        PROJECT_ROOT / "sample_projects" / "fastapi_bad_project",
        include_pytest_execution=False,
    )
    evidence = EvidenceSearchTool(
        RuleRetrievalService(get_default_rule_index_path())
    ).invoke("FastAPI dependency", mode="bm25", top_k=3)

    assert review.release_allowed is False
    assert len(evidence.evidence) == 3
    assert all(item.evidence_id for item in evidence.evidence)


def test_deterministic_risk_and_fix_tools_preserve_blocking_decision() -> None:
    review = ReleaseReviewService().review(
        project_path=PROJECT_ROOT / "sample_projects" / "fastapi_bad_project",
        include_pytest_execution=False,
    )

    risk = RiskAnalysisTool().invoke(review, review.retrieval_evidence)
    plan = FixPlanTool().invoke(review, risk.payload)

    assert risk.llm_attempted is False
    assert risk.payload["release_allowed"] is False
    assert risk.payload["analysis_source"] == "deterministic"
    assert plan
    assert all(step["validation"] for step in plan)
