import json
from pathlib import Path

from releaseguard_agent.agents.release_decision_advice_writer import (
    ADVICE_SCHEMA_VERSION,
    build_advice_payload,
    render_advice_markdown,
    write_advice_artifacts,
)
from releaseguard_agent.agents.release_decision_advisor import (
    ReleaseDecisionAdvisor,
)
from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.core.checker_runner import CheckerRunner
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
        """Return predefined results."""
        return list(self._results)


def _check_result(
    *,
    rule_id: str | None = "RG-DEPS-001",
    status: CheckStatus = CheckStatus.PASSED,
    risk_level: RiskLevel = RiskLevel.INFO,
    title: str = "Example check",
) -> CheckResult:
    return CheckResult(
        checker_name="example_checker",
        status=status,
        risk_level=risk_level,
        title=title,
        message="Example message.",
        evidence=["Example evidence."],
        recommendation="Example recommendation.",
        rule_id=rule_id,
        rule_source="Example source.",
    )


def _advice_result(
    tmp_path: Path,
    *check_results: CheckResult,
):
    advisor = ReleaseDecisionAdvisor.from_rule_index(
        runner=CheckerRunner(
            [
                StaticChecker(
                    name="static_checker",
                    results=tuple(check_results),
                )
            ]
        ),
        index_path=RULE_INDEX_PATH,
    )

    return advisor.advise(tmp_path)


def test_build_advice_payload_contains_stable_fields(
    tmp_path: Path,
) -> None:
    advice_result = _advice_result(
        tmp_path,
        _check_result(rule_id="RG-DEPS-001"),
    )

    payload = build_advice_payload(advice_result=advice_result)

    assert payload["tool"] == "releaseguard-agent"
    assert payload["artifact_type"] == "release-decision-advice"
    assert payload["schema_version"] == ADVICE_SCHEMA_VERSION
    assert payload["project_path"] == str(tmp_path)
    assert payload["workflow_result"]["decision"]["status"] == "ready"
    assert payload["explanation"]["status"] == "ready"


def test_render_advice_markdown_contains_summary_and_explanation(
    tmp_path: Path,
) -> None:
    advice_result = _advice_result(
        tmp_path,
        _check_result(rule_id="RG-DEPS-001"),
    )
    payload = build_advice_payload(advice_result=advice_result)

    markdown = render_advice_markdown(payload)

    assert "# ReleaseGuard Agent Advice" in markdown
    assert "# Release Decision" in markdown
    assert f"- Project: `{tmp_path}`" in markdown
    assert "- Status: `ready`" in markdown
    assert "- Release allowed: `yes`" in markdown
    assert "Release ready" in markdown


def test_write_advice_artifacts_creates_markdown_and_json_files(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "agent-advice"
    advice_result = _advice_result(
        tmp_path,
        _check_result(rule_id="RG-DEPS-001"),
    )

    artifacts = write_advice_artifacts(
        output_dir=output_dir,
        advice_result=advice_result,
    )

    assert artifacts.output_dir == output_dir
    assert artifacts.markdown_path == (
        output_dir / "release_decision_advice.md"
    )
    assert artifacts.json_path == (
        output_dir / "release_decision_advice.json"
    )

    assert artifacts.markdown_path.is_file()
    assert artifacts.json_path.is_file()

    markdown = artifacts.markdown_path.read_text(encoding="utf-8")
    json_payload = json.loads(
        artifacts.json_path.read_text(encoding="utf-8")
    )

    assert "# ReleaseGuard Agent Advice" in markdown
    assert json_payload["tool"] == "releaseguard-agent"
    assert json_payload["schema_version"] == ADVICE_SCHEMA_VERSION
    assert json_payload["explanation"]["status"] == "ready"


def test_writer_does_not_mutate_advice_result(
    tmp_path: Path,
) -> None:
    advice_result = _advice_result(
        tmp_path,
        _check_result(
            rule_id="RG-DOCKER-003",
            status=CheckStatus.FAILED,
            risk_level=RiskLevel.HIGH,
        ),
    )
    original_data = advice_result.to_dict()

    write_advice_artifacts(
        output_dir=tmp_path / "agent-advice",
        advice_result=advice_result,
    )

    assert advice_result.to_dict() == original_data
