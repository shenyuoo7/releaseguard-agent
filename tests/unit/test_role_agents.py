import json
from pathlib import Path

from releaseguard_agent.agent_tools import (
    EvidenceSearchTool,
    FixPlanTool,
    RiskAnalysisTool,
)
from releaseguard_agent.agents.role_agents import (
    EvidenceAgent,
    EvidenceAgentInput,
    FixPlannerAgent,
    FixPlannerAgentInput,
    RiskAgent,
    RiskAgentInput,
    VerifierAgent,
    VerifierAgentInput,
)
from releaseguard_agent.llm import FakeLLMClient, LLMRuntime
from releaseguard_agent.rag import RuleRetrievalService, get_default_rule_index_path
from releaseguard_agent.services import ReleaseReviewService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = PROJECT_ROOT / "sample_projects"


def _blocking_review():
    return ReleaseReviewService().review(
        project_path=SAMPLES / "fastapi_bad_project",
        include_pytest_execution=False,
    )


def test_four_roles_have_independent_contracts_and_state_transfer() -> None:
    review = _blocking_review()
    evidence_output = EvidenceAgent(
        EvidenceSearchTool(
            RuleRetrievalService(get_default_rule_index_path())
        )
    ).run(
        EvidenceAgentInput(
            review=review,
            retrieval_mode="exact",
            minimum_evidence=1,
        )
    )
    cited_id = evidence_output.evidence[0].evidence_id
    response = json.dumps(
        {
            "risk_level": "high",
            "summary": "A deterministic FastAPI dependency blocker remains.",
            "release_status": "release",
            "release_allowed": True,
            "prioritized_risks": [],
            "fix_plan": [],
            "evidence_rule_ids": [evidence_output.evidence[0].rule_id],
            "evidence_ids": [cited_id],
            "unsupported_claims": [],
            "missing_evidence_notes": [],
        }
    )
    runtime = LLMRuntime(
        mode="llm",
        provider="fake",
        model="fake-role-model",
        client=FakeLLMClient([response]),
    )
    risk_output = RiskAgent(RiskAnalysisTool(runtime)).run(
        RiskAgentInput(review=review, evidence=evidence_output.evidence)
    )
    fix_output = FixPlannerAgent(FixPlanTool()).run(
        FixPlannerAgentInput(
            review=review,
            risk=risk_output,
            evidence=evidence_output.evidence,
        )
    )

    assert evidence_output.sufficient is True
    assert evidence_output.supplemental_attempted is True
    assert risk_output.llm_failed is False
    assert risk_output.analysis["release_allowed"] is False
    assert risk_output.evidence_ids == (cited_id,)
    blocking_rule_ids = {
        item.rule_id
        for item in review.check_results
        if item.should_block_release and item.rule_id
    }
    assert blocking_rule_ids.issubset(fix_output.covered_rule_ids)
    assert fix_output.requires_manual_changes is True
    assert any(step["evidence_ids"] for step in fix_output.steps)


def test_verifier_agent_compares_before_and_after_without_mutating_projects(
    tmp_path: Path,
) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    _write_fastapi_project(before, include_fastapi_dependency=False)
    _write_fastapi_project(after, include_fastapi_dependency=True)
    service = ReleaseReviewService()
    before_review = service.review(
        project_path=before, include_pytest_execution=False
    )
    after_review = service.review(
        project_path=after, include_pytest_execution=False
    )

    output = VerifierAgent().run(
        VerifierAgentInput(before=before_review, after=after_review)
    )

    assert output.before_release_allowed is False
    assert output.release_allowed is True
    assert output.status == "resolved"
    assert any(item.startswith("RG-FASTAPI-001::") for item in output.resolved)
    assert output.new == ()


def test_verifier_agent_reports_new_and_unchanged_findings(
    tmp_path: Path,
) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    _write_project_without_dependencies(before, include_fastapi_source=False)
    _write_project_without_dependencies(after, include_fastapi_source=True)
    service = ReleaseReviewService()
    output = VerifierAgent().run(
        VerifierAgentInput(
            before=service.review(
                project_path=before, include_pytest_execution=False
            ),
            after=service.review(
                project_path=after, include_pytest_execution=False
            ),
        )
    )

    assert output.status == "regressed"
    assert any(item.startswith("RG-DEPS-001::") for item in output.unchanged)
    assert any(item.startswith("RG-FASTAPI-001::") for item in output.new)


def _write_fastapi_project(path: Path, *, include_fastapi_dependency: bool) -> None:
    path.mkdir()
    dependencies = "pytest\nfastapi\n" if include_fastapi_dependency else "pytest\n"
    (path / "requirements.txt").write_text(dependencies, encoding="utf-8")
    (path / ".env.example").write_text("APP_ENV=test\n", encoding="utf-8")
    (path / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\n", encoding="utf-8"
    )
    source = path / "src"
    source.mkdir()
    (source / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8"
    )
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "def test_sample():\n    assert True\n", encoding="utf-8"
    )


def _write_project_without_dependencies(
    path: Path,
    *,
    include_fastapi_source: bool,
) -> None:
    path.mkdir()
    (path / ".env.example").write_text("APP_ENV=test\n", encoding="utf-8")
    (path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "def test_sample():\n    assert True\n", encoding="utf-8"
    )
    if include_fastapi_source:
        source = path / "src"
        source.mkdir()
        (source / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8"
        )
