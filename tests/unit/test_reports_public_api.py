from releaseguard_agent import reports
from releaseguard_agent.reports import (
    CHECKLIST_FILE_NAME,
    CHECKLIST_SCHEMA_VERSION,
    ReleaseChecklistArtifacts,
    ReportArtifacts,
    build_release_checklist_payload,
    build_report_payload,
    render_markdown_report,
    render_release_checklist_markdown,
    write_release_checklist_artifact,
    write_report_artifacts,
)


def test_reports_public_api_exports_report_writer_contract():
    assert reports.ReportArtifacts is ReportArtifacts
    assert reports.build_report_payload is build_report_payload
    assert reports.render_markdown_report is render_markdown_report
    assert reports.write_report_artifacts is write_report_artifacts


def test_reports_public_api_exports_release_checklist_writer_contract():
    assert reports.CHECKLIST_FILE_NAME == CHECKLIST_FILE_NAME
    assert reports.CHECKLIST_SCHEMA_VERSION == CHECKLIST_SCHEMA_VERSION
    assert reports.ReleaseChecklistArtifacts is ReleaseChecklistArtifacts
    assert reports.build_release_checklist_payload is build_release_checklist_payload
    assert reports.render_release_checklist_markdown is render_release_checklist_markdown
    assert reports.write_release_checklist_artifact is write_release_checklist_artifact


def test_reports_public_api_all_is_explicit():
    assert reports.__all__ == [
        "CHECKLIST_FILE_NAME",
        "CHECKLIST_SCHEMA_VERSION",
        "ReleaseChecklistArtifacts",
        "ReportArtifacts",
        "build_release_checklist_payload",
        "build_report_payload",
        "render_markdown_report",
        "render_release_checklist_markdown",
        "write_release_checklist_artifact",
        "write_report_artifacts",
    ]
