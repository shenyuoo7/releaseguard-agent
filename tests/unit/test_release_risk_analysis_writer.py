import json
from pathlib import Path

from releaseguard_agent.agents.release_decision_advisor import (
    ReleaseDecisionAdvisor,
)
from releaseguard_agent.agents.release_risk_analysis_agent import (
    ReleaseRiskAnalysisAgent,
    ReleaseRiskAnalysisContext,
)
from releaseguard_agent.agents.release_risk_analysis_writer import (
    RELEASE_RISK_ANALYSIS_ARTIFACT_SCHEMA_VERSION,
    build_release_risk_analysis_payload,
    render_release_fix_plan_markdown,
    render_release_risk_analysis_markdown,
    write_release_risk_analysis_artifacts,
)
from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.core.checker_runner import CheckerRunner
from releaseguard_agent.llm import FakeLLMClient
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RULE_INDEX_PATH = (
    PROJECT_ROOT
    / "knowledge_base"
    / "release_rules"
    / "rule_index.md"
)


class StaticChecker(BaseChecker):
    """Test checker that returns predefined check results."""

    description = "Returns predefined check results."

    def __init__(
        self,
        *,
        name: str,
        results: tuple[CheckResult, ...],
    ) -> None:
        self.name = name
        self._results = results

    def run(self, project_path: Path) -> list[CheckResult]:
        """Return predefined check results."""
        return list(self._results)


def _check_result() -> CheckResult:
    return CheckResult(
        checker_name="docker_checker",
        status=CheckStatus.FAILED,
        risk_level=RiskLevel.HIGH,
        title="Dockerfile release contract failed",
        message="Docker release intent exists but Dockerfile is invalid.",
        evidence=["Dockerfile is missing a required release contract."],
        recommendation="Add a valid Dockerfile for the release path.",
        rule_id="RG-DOCKER-003",
        rule_source="ReleaseGuard rule knowledge base",
    )


def _analysis_result(tmp_path: Path):
    advisor = ReleaseDecisionAdvisor.from_rule_index(
        runner=CheckerRunner(
            [
                StaticChecker(
                    name="static_checker",
                    results=(_check_result(),),
                )
            ]
        ),
        index_path=RULE_INDEX_PATH,
    )
    advice_result = advisor.advise(tmp_path)

    client = FakeLLMClient(
        responses=[
            json.dumps(
                {
                    "risk_level": "high",
                    "summary": (
                        "Release is blocked by a Docker release contract "
                        "risk."
                    ),
                    "release_status": "blocked",
                    "release_allowed": False,
                    "prioritized_risks": [
                        {
                            "rule_id": "RG-DOCKER-003",
                            "title": "Docker release contract failed",
                            "severity": "high",
                            "reason": (
                                "A release with container intent needs a "
                                "valid Dockerfile."
                            ),
                            "evidence": [
                                "Deterministic checker reported "
                                "RG-DOCKER-003."
                            ],
                        }
                    ],
                    "fix_plan": [
                        {
                            "priority": 1,
                            "title": "Fix Dockerfile release contract",
                            "action": "Create or repair the root Dockerfile.",
                            "rule_ids": ["RG-DOCKER-003"],
                            "validation": (
                                "Run ReleaseGuard again and confirm the "
                                "check passes."
                            ),
                        }
                    ],
                    "evidence_rule_ids": ["RG-DOCKER-003"],
                    "unsupported_claims": [],
                    "missing_evidence_notes": [],
                }
            )
        ]
    )
    agent = ReleaseRiskAnalysisAgent(
        llm_client=client,
        model="fake-risk-model",
    )

    return agent.analyze(
        ReleaseRiskAnalysisContext(
            advice_result=advice_result,
            release_report_markdown="# Release Report\nBlocked.",
            release_checklist_markdown="# Release Checklist\nFix Dockerfile.",
            trace_payload={
                "run_id": "run-001",
            },
        )
    )


def test_build_payload_contains_stable_fields(tmp_path: Path) -> None:
    result = _analysis_result(tmp_path)

    payload = build_release_risk_analysis_payload(result=result)

    assert payload["tool"] == "releaseguard-agent"
    assert payload["artifact_type"] == "release-risk-analysis"
    assert (
        payload["schema_version"]
        == RELEASE_RISK_ANALYSIS_ARTIFACT_SCHEMA_VERSION
    )
    assert payload["project_path"] == str(tmp_path)
    assert payload["decision"]["release_status"] == "blocked"
    assert payload["decision"]["release_allowed"] is False
    assert payload["decision"]["risk_level"] == "high"
    assert payload["analysis"]["evidence_rule_ids"] == ["RG-DOCKER-003"]
    assert payload["llm_response"]["provider"] == "fake"
    assert payload["prompt_messages"][0]["role"] == "system"


def test_render_risk_analysis_markdown_contains_required_sections(
    tmp_path: Path,
) -> None:
    payload = build_release_risk_analysis_payload(
        result=_analysis_result(tmp_path),
    )

    markdown = render_release_risk_analysis_markdown(payload)

    assert "# ReleaseGuard Agent Risk Analysis" in markdown
    assert f"- Project: `{tmp_path}`" in markdown
    assert "- Deterministic status: `blocked`" in markdown
    assert "- Release allowed: `no`" in markdown
    assert "## Summary" in markdown
    assert "## Prioritized Risks" in markdown
    assert "`RG-DOCKER-003` Docker release contract failed" in markdown
    assert "## Fix Plan" in markdown
    assert "Fix Dockerfile release contract" in markdown
    assert "## Guardrail Notes" in markdown
    assert "No guardrail notes." in markdown


def test_render_fix_plan_markdown_contains_fix_steps(
    tmp_path: Path,
) -> None:
    payload = build_release_risk_analysis_payload(
        result=_analysis_result(tmp_path),
    )

    markdown = render_release_fix_plan_markdown(payload)

    assert "# ReleaseGuard Agent Fix Plan" in markdown
    assert "- Deterministic status: `blocked`" in markdown
    assert "1. Fix Dockerfile release contract" in markdown
    assert "Create or repair the root Dockerfile." in markdown
    assert "Run ReleaseGuard again and confirm the check passes." in markdown
    assert "## Evidence Rule IDs" in markdown
    assert "- RG-DOCKER-003" in markdown


def test_write_artifacts_creates_markdown_json_and_fix_plan(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "agent-risk"
    result = _analysis_result(tmp_path)

    artifacts = write_release_risk_analysis_artifacts(
        output_dir=output_dir,
        result=result,
    )

    assert artifacts.output_dir == output_dir
    assert artifacts.risk_analysis_markdown_path == (
        output_dir / "agent_risk_analysis.md"
    )
    assert artifacts.decision_json_path == (
        output_dir / "agent_decision.json"
    )
    assert artifacts.fix_plan_markdown_path == (
        output_dir / "agent_fix_plan.md"
    )

    assert artifacts.risk_analysis_markdown_path.is_file()
    assert artifacts.decision_json_path.is_file()
    assert artifacts.fix_plan_markdown_path.is_file()

    risk_markdown = artifacts.risk_analysis_markdown_path.read_text(
        encoding="utf-8",
    )
    decision_payload = json.loads(
        artifacts.decision_json_path.read_text(encoding="utf-8")
    )
    fix_plan_markdown = artifacts.fix_plan_markdown_path.read_text(
        encoding="utf-8",
    )

    assert "# ReleaseGuard Agent Risk Analysis" in risk_markdown
    assert "# ReleaseGuard Agent Fix Plan" in fix_plan_markdown
    assert decision_payload["schema_version"] == (
        RELEASE_RISK_ANALYSIS_ARTIFACT_SCHEMA_VERSION
    )
    assert decision_payload["decision"]["release_status"] == "blocked"


def test_writer_does_not_mutate_analysis_result(tmp_path: Path) -> None:
    result = _analysis_result(tmp_path)
    original_data = result.to_dict()

    write_release_risk_analysis_artifacts(
        output_dir=tmp_path / "agent-risk",
        result=result,
    )

    assert result.to_dict() == original_data
