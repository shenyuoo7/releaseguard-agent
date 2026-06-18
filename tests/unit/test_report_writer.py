import json

from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.reports.report_writer import (
    build_report_payload,
    render_markdown_report,
    write_report_artifacts,
)


def create_sample_result():
    return CheckResult(
        checker_name="dependency_checker",
        status=CheckStatus.PASSED,
        risk_level=RiskLevel.INFO,
        title="Dependency declaration found",
        message="The project declares Python dependencies.",
        evidence=["Found dependency file: requirements.txt"],
        recommendation=None,
        rule_id="RG-DEPS-001",
        rule_source="The Twelve-Factor App - Dependencies",
        file_path="requirements.txt",
        metadata={
            "support_level": "source-backed",
            "blocking_policy": "block",
        },
    )


def create_sample_summary():
    return {
        "total": 1,
        "passed": 1,
        "failed": 0,
        "warning": 0,
        "skipped": 0,
        "blocking": 0,
        "status_counts": {
            "passed": 1,
            "failed": 0,
            "warning": 0,
            "skipped": 0,
        },
        "risk_counts": {
            "info": 1,
            "low": 0,
            "medium": 0,
            "high": 0,
            "critical": 0,
        },
    }


def test_build_report_payload_contains_summary_and_results(tmp_path):
    result = create_sample_result()
    summary = create_sample_summary()

    payload = build_report_payload(
        project_path=tmp_path,
        include_pytest_execution=False,
        summary=summary,
        results=[result],
    )

    assert payload["tool"] == "releaseguard-agent"
    assert payload["project_path"] == str(tmp_path)
    assert payload["include_pytest_execution"] is False
    assert payload["summary"]["blocking"] == 0
    assert payload["results"][0]["rule_id"] == "RG-DEPS-001"
    assert payload["results"][0]["should_block_release"] is False


def test_render_markdown_report_contains_summary_and_result(tmp_path):
    payload = build_report_payload(
        project_path=tmp_path,
        include_pytest_execution=False,
        summary=create_sample_summary(),
        results=[create_sample_result()],
    )

    markdown = render_markdown_report(payload)

    assert "# ReleaseGuard Report" in markdown
    assert "## Summary" in markdown
    assert "| Blocking | 0 |" in markdown
    assert "RG-DEPS-001" in markdown
    assert "Dependency declaration found" in markdown
    assert "Found dependency file: requirements.txt" in markdown


def test_write_report_artifacts_creates_markdown_and_json_files(tmp_path):
    output_dir = tmp_path / "reports"
    payload = build_report_payload(
        project_path=tmp_path,
        include_pytest_execution=False,
        summary=create_sample_summary(),
        results=[create_sample_result()],
    )

    artifacts = write_report_artifacts(
        output_dir=output_dir,
        payload=payload,
    )

    assert artifacts.output_dir == output_dir
    assert artifacts.markdown_path == output_dir / "release_report.md"
    assert artifacts.json_path == output_dir / "check_result.json"

    assert artifacts.markdown_path.is_file()
    assert artifacts.json_path.is_file()

    markdown = artifacts.markdown_path.read_text(encoding="utf-8")
    json_payload = json.loads(artifacts.json_path.read_text(encoding="utf-8"))

    assert "ReleaseGuard Report" in markdown
    assert json_payload["tool"] == "releaseguard-agent"
    assert json_payload["summary"]["total"] == 1