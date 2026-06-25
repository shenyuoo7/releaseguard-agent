from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.reports.release_checklist_writer import (
    CHECKLIST_FILE_NAME,
    CHECKLIST_SCHEMA_VERSION,
    ReleaseChecklistArtifacts,
    build_release_checklist_payload,
    render_release_checklist_markdown,
    write_release_checklist_artifact,
)


def create_result(
    *,
    checker_name: str,
    status: CheckStatus,
    risk_level: RiskLevel,
    title: str,
    message: str,
    rule_id: str,
    evidence: list[str] | None = None,
    recommendation: str | None = None,
    rule_source: str | None = None,
    file_path: str | None = None,
    metadata: dict[str, object] | None = None,
) -> CheckResult:
    return CheckResult(
        checker_name=checker_name,
        status=status,
        risk_level=risk_level,
        title=title,
        message=message,
        evidence=evidence or [],
        recommendation=recommendation,
        rule_id=rule_id,
        rule_source=rule_source,
        file_path=file_path,
        metadata=metadata or {},
    )


def create_summary() -> dict[str, object]:
    return {
        "total": 5,
        "passed": 1,
        "failed": 2,
        "warning": 1,
        "skipped": 1,
        "blocking": 1,
        "status_counts": {
            "passed": 1,
            "failed": 2,
            "warning": 1,
            "skipped": 1,
        },
        "risk_counts": {
            "info": 2,
            "low": 0,
            "medium": 2,
            "high": 1,
            "critical": 0,
        },
    }


def create_grouped_results() -> list[CheckResult]:
    return [
        create_result(
            checker_name="docker_checker",
            status=CheckStatus.FAILED,
            risk_level=RiskLevel.HIGH,
            title="Docker healthcheck is missing",
            message="The Dockerfile does not define a healthcheck.",
            rule_id="RG-DOCKER-001",
            evidence=["Dockerfile has no HEALTHCHECK instruction."],
            recommendation="Add a lightweight Docker HEALTHCHECK instruction.",
            rule_source="ReleaseGuard Docker production readiness",
            file_path="Dockerfile",
        ),
        create_result(
            checker_name="pytest_config_checker",
            status=CheckStatus.WARNING,
            risk_level=RiskLevel.MEDIUM,
            title="Pytest configuration is incomplete",
            message="The project has tests but no pytest configuration.",
            rule_id="RG-TEST-002",
            evidence=["No pytest.ini, pyproject.toml, or setup.cfg was found."],
            recommendation="Add a minimal pytest configuration.",
            file_path="pytest.ini",
        ),
        create_result(
            checker_name="dependency_checker",
            status=CheckStatus.FAILED,
            risk_level=RiskLevel.MEDIUM,
            title="Dependency lock file is missing",
            message="The project declares dependencies without a lock file.",
            rule_id="RG-DEPS-002",
            evidence=["Found requirements.txt but no lock file."],
            recommendation="Add or document the dependency locking strategy.",
            file_path="requirements.txt",
        ),
        create_result(
            checker_name="dependency_checker",
            status=CheckStatus.PASSED,
            risk_level=RiskLevel.INFO,
            title="Dependency declaration found",
            message="The project declares Python dependencies.",
            rule_id="RG-DEPS-001",
            evidence=["Found dependency file: requirements.txt"],
            file_path="requirements.txt",
        ),
        create_result(
            checker_name="pytest_execution_checker",
            status=CheckStatus.SKIPPED,
            risk_level=RiskLevel.INFO,
            title="Pytest execution skipped",
            message="Pytest execution was disabled for this run.",
            rule_id="RG-TEST-005",
            recommendation="Run with pytest execution enabled before release.",
        ),
    ]


def test_build_release_checklist_payload_groups_results_deterministically(tmp_path):
    payload = build_release_checklist_payload(
        project_path=tmp_path,
        include_pytest_execution=True,
        summary=create_summary(),
        results=create_grouped_results(),
    )

    assert payload["tool"] == "releaseguard-agent"
    assert payload["artifact_type"] == "releaseguard_release_checklist"
    assert payload["schema_version"] == CHECKLIST_SCHEMA_VERSION
    assert payload["project_path"] == str(tmp_path)
    assert payload["include_pytest_execution"] is True
    assert payload["summary"]["blocking"] == 1

    sections = payload["sections"]

    assert [
        item["rule_id"]
        for item in sections["blocking_fixes"]
    ] == ["RG-DOCKER-001"]
    assert [
        item["rule_id"]
        for item in sections["warnings_to_review"]
    ] == ["RG-TEST-002", "RG-DEPS-002"]
    assert [
        item["rule_id"]
        for item in sections["passed_checks"]
    ] == ["RG-DEPS-001"]
    assert [
        item["rule_id"]
        for item in sections["skipped_checks"]
    ] == ["RG-TEST-005"]


def test_render_release_checklist_markdown_contains_summary_and_sections(tmp_path):
    payload = build_release_checklist_payload(
        project_path=tmp_path,
        include_pytest_execution=True,
        summary=create_summary(),
        results=create_grouped_results(),
    )

    markdown = render_release_checklist_markdown(payload)

    assert "# ReleaseGuard Release Checklist" in markdown
    assert f"- Schema version: `{CHECKLIST_SCHEMA_VERSION}`" in markdown
    assert "- Pytest execution: `enabled`" in markdown
    assert "| Blocking | 1 |" in markdown

    assert "## Blocking fixes" in markdown
    assert "- [ ] `RG-DOCKER-001` Docker healthcheck is missing" in markdown
    assert "Add a lightweight Docker HEALTHCHECK instruction." in markdown

    assert "## Warnings to review" in markdown
    assert "- [ ] `RG-TEST-002` Pytest configuration is incomplete" in markdown
    assert "- [ ] `RG-DEPS-002` Dependency lock file is missing" in markdown

    assert "## Passed checks" in markdown
    assert "- [x] `RG-DEPS-001` Dependency declaration found" in markdown

    assert "## Skipped checks" in markdown
    assert "- [ ] `RG-TEST-005` Pytest execution skipped" in markdown


def test_write_release_checklist_artifact_creates_markdown_file(tmp_path):
    output_dir = tmp_path / "reports"
    payload = build_release_checklist_payload(
        project_path=tmp_path,
        include_pytest_execution=False,
        summary=create_summary(),
        results=create_grouped_results(),
    )

    artifacts = write_release_checklist_artifact(
        output_dir=output_dir,
        payload=payload,
    )

    assert isinstance(artifacts, ReleaseChecklistArtifacts)
    assert artifacts.output_dir == output_dir
    assert artifacts.markdown_path == output_dir / CHECKLIST_FILE_NAME
    assert artifacts.markdown_path.is_file()

    markdown = artifacts.markdown_path.read_text(encoding="utf-8")

    assert markdown == render_release_checklist_markdown(payload)
    assert "ReleaseGuard Release Checklist" in markdown


def test_build_release_checklist_payload_copies_mutable_inputs(tmp_path):
    summary = {
        "total": 1,
        "passed": 1,
        "failed": 0,
        "warning": 0,
        "skipped": 0,
        "blocking": 0,
    }
    result = create_result(
        checker_name="dependency_checker",
        status=CheckStatus.PASSED,
        risk_level=RiskLevel.INFO,
        title="Dependency declaration found",
        message="The project declares Python dependencies.",
        rule_id="RG-DEPS-001",
        evidence=["before mutation"],
        metadata={
            "notes": ["before mutation"],
        },
    )

    payload = build_release_checklist_payload(
        project_path=tmp_path,
        include_pytest_execution=False,
        summary=summary,
        results=[result],
    )

    summary["total"] = 99
    result.evidence.append("after mutation")
    result.metadata["notes"].append("after mutation")

    passed_item = payload["sections"]["passed_checks"][0]

    assert payload["summary"]["total"] == 1
    assert passed_item["evidence"] == ["before mutation"]
    assert passed_item["metadata"]["notes"] == ["before mutation"]
