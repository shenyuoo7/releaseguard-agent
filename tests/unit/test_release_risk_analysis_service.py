import json
from pathlib import Path

from releaseguard_agent.agents.release_risk_analysis_service import (
    ReleaseRiskAnalysisService,
)
from releaseguard_agent.llm import FakeLLMClient


def _model_response() -> str:
    return json.dumps(
        {
            "risk_level": "high",
            "summary": "Release is blocked by missing release evidence.",
            "release_status": "blocked",
            "release_allowed": False,
            "prioritized_risks": [
                {
                    "rule_id": "RG-DEPS-001",
                    "title": "Dependency declaration missing",
                    "severity": "high",
                    "reason": "The project has no dependency declaration.",
                    "evidence": [
                        "Deterministic checks reported a blocker."
                    ],
                }
            ],
            "fix_plan": [
                {
                    "priority": 1,
                    "title": "Add dependency declaration",
                    "action": "Create requirements.txt or pyproject.toml.",
                    "rule_ids": ["RG-DEPS-001"],
                    "validation": "Run ReleaseGuard again.",
                }
            ],
            "evidence_rule_ids": ["RG-DEPS-001"],
            "unsupported_claims": [],
            "missing_evidence_notes": [],
        }
    )


def test_service_runs_analysis_and_writes_artifacts(
    tmp_path: Path,
) -> None:
    client = FakeLLMClient(responses=[_model_response()])
    service = ReleaseRiskAnalysisService.from_project_defaults(
        llm_client=client,
        include_pytest_execution=False,
        model="fake-risk-model",
    )

    result = service.run(
        project_path=tmp_path,
        output_dir=tmp_path / "agent-risk",
        release_report_markdown="# Release Report\nBlocked.",
        trace_payload={
            "run_id": "run-001",
        },
    )

    assert result.project_path == tmp_path
    assert result.include_pytest_execution is False
    assert result.analysis_result.analysis.release_status == "blocked"
    assert result.artifacts.risk_analysis_markdown_path.is_file()
    assert result.artifacts.decision_json_path.is_file()
    assert result.artifacts.fix_plan_markdown_path.is_file()

    assert client.calls[0].model == "fake-risk-model"
    assert client.calls[0].response_format == "json_object"


def test_service_result_can_be_converted_to_dict(
    tmp_path: Path,
) -> None:
    client = FakeLLMClient(responses=[_model_response()])
    service = ReleaseRiskAnalysisService.from_project_defaults(
        llm_client=client,
        include_pytest_execution=False,
    )

    result = service.run(
        project_path=tmp_path,
        output_dir=tmp_path / "agent-risk",
    )

    data = result.to_dict()

    assert data["project_path"] == str(tmp_path)
    assert data["include_pytest_execution"] is False
    assert data["analysis_result"]["analysis"]["risk_level"] == "high"
    assert data["artifacts"]["decision_json_path"].endswith(
        "agent_decision.json"
    )


def test_service_keeps_configuration(tmp_path: Path) -> None:
    client = FakeLLMClient(responses=[_model_response()])
    service = ReleaseRiskAnalysisService.from_project_defaults(
        llm_client=client,
        include_pytest_execution=False,
    )

    result = service.run(
        project_path=tmp_path,
        output_dir=tmp_path / "agent-risk",
    )

    assert service.rule_index_path.is_file()
    assert service.include_pytest_execution is False
    assert result.advice_result.decision.summary["total"] > 0
