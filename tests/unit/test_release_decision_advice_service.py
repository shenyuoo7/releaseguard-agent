import json
from pathlib import Path

from releaseguard_agent.agents.release_decision_advice_service import (
    ReleaseDecisionAdviceService,
    get_default_rule_index_path,
)


def test_default_rule_index_path_points_to_current_rule_index() -> None:
    rule_index_path = get_default_rule_index_path()

    assert rule_index_path.is_file()
    assert rule_index_path.name == "rule_index.md"
    assert rule_index_path.parent.name == "release_rules"


def test_service_keeps_pytest_execution_toggle() -> None:
    enabled_service = ReleaseDecisionAdviceService.from_project_defaults(
        include_pytest_execution=True,
    )
    disabled_service = ReleaseDecisionAdviceService.from_project_defaults(
        include_pytest_execution=False,
    )

    assert enabled_service.include_pytest_execution is True
    assert disabled_service.include_pytest_execution is False


def test_service_runs_default_non_dynamic_advice_and_writes_artifacts(
    tmp_path: Path,
) -> None:
    service = ReleaseDecisionAdviceService.from_project_defaults(
        include_pytest_execution=False,
    )
    output_dir = tmp_path / "agent-advice"

    result = service.run(
        project_path=tmp_path,
        output_dir=output_dir,
    )

    assert result.project_path == tmp_path
    assert result.output_dir == output_dir
    assert result.include_pytest_execution is False
    assert result.advice_result.decision.summary["total"] > 0
    assert result.artifacts.markdown_path.is_file()
    assert result.artifacts.json_path.is_file()

    markdown = result.artifacts.markdown_path.read_text(encoding="utf-8")
    json_payload = json.loads(
        result.artifacts.json_path.read_text(encoding="utf-8")
    )

    assert "# ReleaseGuard Agent Advice" in markdown
    assert json_payload["artifact_type"] == "release-decision-advice"
    assert json_payload["project_path"] == str(tmp_path)


def test_service_result_can_be_converted_to_dict(
    tmp_path: Path,
) -> None:
    service = ReleaseDecisionAdviceService.from_project_defaults(
        include_pytest_execution=False,
    )

    result = service.run(
        project_path=tmp_path,
        output_dir=tmp_path / "agent-advice",
    )

    data = result.to_dict()

    assert data["project_path"] == str(tmp_path)
    assert data["include_pytest_execution"] is False
    assert data["advice_result"]["workflow_result"]["decision"]["summary"][
        "total"
    ] > 0
    assert data["artifacts"]["markdown_path"].endswith(
        "release_decision_advice.md"
    )
    assert data["artifacts"]["json_path"].endswith(
        "release_decision_advice.json"
    )


def test_service_uses_configured_rule_index_path(
    tmp_path: Path,
) -> None:
    rule_index_path = get_default_rule_index_path()
    service = ReleaseDecisionAdviceService(
        rule_index_path=rule_index_path,
        include_pytest_execution=False,
    )

    result = service.run(
        project_path=tmp_path,
        output_dir=tmp_path / "agent-advice",
    )

    assert service.rule_index_path == rule_index_path
    assert result.advice_result.decision.summary["total"] > 0


def test_service_result_to_dict_is_stable(
    tmp_path: Path,
) -> None:
    service = ReleaseDecisionAdviceService.from_project_defaults(
        include_pytest_execution=False,
    )
    result = service.run(
        project_path=tmp_path,
        output_dir=tmp_path / "agent-advice",
    )

    first_data = result.to_dict()
    second_data = result.to_dict()

    assert second_data == first_data
