import json
from pathlib import Path

from releaseguard_agent.agents import (
    ADVICE_SCHEMA_VERSION,
    ReleaseDecisionAdviceService,
    build_advice_payload,
    render_advice_markdown,
    write_advice_artifacts,
)


def test_public_agent_advice_service_writes_markdown_and_json(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "sample-project"
    output_dir = tmp_path / "agent-advice"
    project_path.mkdir()

    service = ReleaseDecisionAdviceService.from_project_defaults(
        include_pytest_execution=False,
    )

    result = service.run(
        project_path=project_path,
        output_dir=output_dir,
    )

    assert result.project_path == project_path
    assert result.output_dir == output_dir
    assert result.include_pytest_execution is False
    assert result.artifacts.markdown_path == (
        output_dir / "release_decision_advice.md"
    )
    assert result.artifacts.json_path == (
        output_dir / "release_decision_advice.json"
    )

    markdown = result.artifacts.markdown_path.read_text(encoding="utf-8")
    payload = json.loads(
        result.artifacts.json_path.read_text(encoding="utf-8")
    )

    assert "# ReleaseGuard Agent Advice" in markdown
    assert payload["tool"] == "releaseguard-agent"
    assert payload["artifact_type"] == "release-decision-advice"
    assert payload["schema_version"] == ADVICE_SCHEMA_VERSION
    assert payload["project_path"] == str(project_path)
    assert payload["explanation"]["status"] == (
        result.advice_result.explanation.status.value
    )


def test_public_agent_advice_writer_utilities_render_stable_artifacts(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "sample-project"
    service_output_dir = tmp_path / "service-advice"
    writer_output_dir = tmp_path / "writer-advice"
    project_path.mkdir()

    service = ReleaseDecisionAdviceService.from_project_defaults(
        include_pytest_execution=False,
    )
    service_result = service.run(
        project_path=project_path,
        output_dir=service_output_dir,
    )

    payload = build_advice_payload(
        advice_result=service_result.advice_result,
    )
    markdown = render_advice_markdown(payload)
    artifacts = write_advice_artifacts(
        output_dir=writer_output_dir,
        advice_result=service_result.advice_result,
    )

    assert payload["tool"] == "releaseguard-agent"
    assert payload["artifact_type"] == "release-decision-advice"
    assert payload["schema_version"] == ADVICE_SCHEMA_VERSION
    assert payload["project_path"] == str(project_path)
    assert "# ReleaseGuard Agent Advice" in markdown

    assert artifacts.markdown_path.is_file()
    assert artifacts.json_path.is_file()
    assert artifacts.output_dir == writer_output_dir

    written_payload = json.loads(
        artifacts.json_path.read_text(encoding="utf-8")
    )

    assert written_payload == payload
