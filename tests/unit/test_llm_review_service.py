import json
from pathlib import Path

import pytest

from releaseguard_agent.llm import FakeLLMClient, LLMRuntime
from releaseguard_agent.services import (
    LLMAnalysisUnavailableError,
    LLMReviewService,
    ReleaseReviewService,
)


def _response() -> str:
    return json.dumps(
        {
            "risk_level": "high",
            "summary": "Deterministic checks found release blockers.",
            "release_status": "blocked",
            "release_allowed": False,
            "prioritized_risks": [],
            "fix_plan": [],
            "evidence_rule_ids": [],
            "evidence_ids": [],
            "unsupported_claims": [],
            "missing_evidence_notes": [],
        }
    )


def test_llm_review_service_reuses_existing_scan_and_writes_artifacts(
    tmp_path: Path,
) -> None:
    review = ReleaseReviewService().review(
        project_path=tmp_path,
        include_pytest_execution=False,
    )
    client = FakeLLMClient(responses=[_response()])
    runtime = LLMRuntime(
        mode="llm",
        provider="fake",
        model="fake-model",
        client=client,
    )

    result = LLMReviewService(runtime).analyze(
        review=review,
        output_dir=tmp_path / "llm",
    )

    assert result.provider == "fake"
    assert result.model == "fake-model"
    assert len(client.calls) == 1
    assert result.artifacts.decision_json_path.is_file()
    assert result.artifacts.fix_plan_markdown_path.is_file()


def test_llm_review_service_refuses_deterministic_runtime(
    tmp_path: Path,
) -> None:
    review = ReleaseReviewService().review(
        project_path=tmp_path,
        include_pytest_execution=False,
    )
    runtime = LLMRuntime(
        mode="deterministic",
        provider="deterministic",
        model=None,
        client=None,
    )

    with pytest.raises(LLMAnalysisUnavailableError):
        LLMReviewService(runtime).analyze(
            review=review,
            output_dir=tmp_path / "llm",
        )

    assert not (tmp_path / "llm").exists()
