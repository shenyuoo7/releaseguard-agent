import json

from releaseguard_agent.cli.main import (
    EXIT_BLOCKING_ISSUES,
    EXIT_SUCCESS,
    EXIT_USAGE_ERROR,
    main,
)
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)


def create_release_ready_project(project_path):
    requirements_file = project_path / "requirements.txt"
    requirements_file.write_text("pytest\n", encoding="utf-8")

    env_example_file = project_path / ".env.example"
    env_example_file.write_text(
        "APP_ENV=local\n",
        encoding="utf-8",
    )

    tests_dir = project_path / "tests"
    tests_dir.mkdir()

    test_file = tests_dir / "test_sample.py"
    test_file.write_text(
        "def test_sample():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    pytest_ini = project_path / "pytest.ini"
    pytest_ini.write_text(
        "[pytest]\n"
        "testpaths = tests\n",
        encoding="utf-8",
    )


def test_check_command_runs_default_non_dynamic_checks(
    tmp_path,
    capsys,
):
    create_release_ready_project(tmp_path)

    exit_code = main(
        ["check", str(tmp_path), "--skip-pytest-execution"]
    )

    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    assert "ReleaseGuard Agent" in captured.out
    assert "blocking: 0" in captured.out
    assert "dependency_checker" in captured.out
    assert "pytest_config_checker" in captured.out
    assert "docker_checker" in captured.out
    assert "docker_style_checker" in captured.out


def test_check_command_returns_one_when_blocking_findings_exist(
    tmp_path,
    capsys,
):
    exit_code = main(
        ["check", str(tmp_path), "--skip-pytest-execution"]
    )

    captured = capsys.readouterr()

    assert exit_code == EXIT_BLOCKING_ISSUES
    assert "ReleaseGuard Agent" in captured.out
    assert "blocking:" in captured.out
    assert "[FAILED]" in captured.out


def test_check_command_outputs_json(tmp_path, capsys):
    create_release_ready_project(tmp_path)

    exit_code = main(
        [
            "check",
            str(tmp_path),
            "--skip-pytest-execution",
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == EXIT_SUCCESS
    assert payload["tool"] == "releaseguard-agent"
    assert payload["project_path"] == str(tmp_path.resolve())
    assert payload["include_pytest_execution"] is False
    assert payload["summary"]["blocking"] == 0
    assert len(payload["results"]) >= 1


def test_check_command_writes_report_artifacts(
    tmp_path,
    capsys,
):
    create_release_ready_project(tmp_path)
    output_dir = tmp_path / "generated-reports"

    exit_code = main(
        [
            "check",
            str(tmp_path),
            "--skip-pytest-execution",
            "--format",
            "json",
            "--output-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    terminal_payload = json.loads(captured.out)

    markdown_path = output_dir / "release_report.md"
    json_path = output_dir / "check_result.json"

    assert exit_code == EXIT_SUCCESS
    assert captured.err == ""

    assert markdown_path.is_file()
    assert json_path.is_file()

    markdown = markdown_path.read_text(encoding="utf-8")
    file_payload = json.loads(
        json_path.read_text(encoding="utf-8")
    )

    assert "# ReleaseGuard Report" in markdown
    assert "## Summary" in markdown
    assert "RG-DEPS-001" in markdown

    assert file_payload["tool"] == "releaseguard-agent"
    assert file_payload["project_path"] == str(tmp_path.resolve())
    assert file_payload["summary"]["blocking"] == 0

    assert terminal_payload == file_payload


def test_check_command_writes_release_checklist_artifact(
    tmp_path,
    capsys,
):
    create_release_ready_project(tmp_path)
    checklist_output_dir = tmp_path / "release-checklist"

    exit_code = main(
        [
            "check",
            str(tmp_path),
            "--skip-pytest-execution",
            "--format",
            "json",
            "--checklist-output-dir",
            str(checklist_output_dir),
        ]
    )

    captured = capsys.readouterr()
    terminal_payload = json.loads(captured.out)

    markdown_path = checklist_output_dir / "release_checklist.md"

    assert exit_code == EXIT_SUCCESS
    assert captured.err == ""

    assert markdown_path.is_file()

    markdown = markdown_path.read_text(encoding="utf-8")

    assert terminal_payload["summary"]["blocking"] == 0
    assert "# ReleaseGuard Release Checklist" in markdown
    assert "## Summary" in markdown
    assert "## Blocking fixes" in markdown
    assert "## Warnings to review" in markdown
    assert "## Passed checks" in markdown
    assert "## Skipped checks" in markdown
    assert "RG-DEPS-001" in markdown


def test_check_command_writes_agent_advice_artifacts(
    tmp_path,
    capsys,
):
    create_release_ready_project(tmp_path)
    advice_output_dir = tmp_path / "agent-advice"

    exit_code = main(
        [
            "check",
            str(tmp_path),
            "--skip-pytest-execution",
            "--format",
            "json",
            "--agent-advice-output-dir",
            str(advice_output_dir),
        ]
    )

    captured = capsys.readouterr()
    terminal_payload = json.loads(captured.out)

    markdown_path = advice_output_dir / "release_decision_advice.md"
    json_path = advice_output_dir / "release_decision_advice.json"

    assert exit_code == EXIT_SUCCESS
    assert captured.err == ""

    assert markdown_path.is_file()
    assert json_path.is_file()

    markdown = markdown_path.read_text(encoding="utf-8")
    advice_payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert terminal_payload["summary"]["blocking"] == 0
    assert "# ReleaseGuard Agent Advice" in markdown
    assert advice_payload["artifact_type"] == "release-decision-advice"
    assert advice_payload["project_path"] == str(tmp_path.resolve())
    assert advice_payload["workflow_result"]["decision"]["status"] == "ready"
    assert advice_payload["explanation"]["status"] == "ready"


def test_check_command_writes_trace_artifact(
    tmp_path,
    capsys,
):
    create_release_ready_project(tmp_path)
    report_output_dir = tmp_path / "reports"
    checklist_output_dir = tmp_path / "release-checklist"
    advice_output_dir = tmp_path / "agent-advice"
    trace_output_dir = tmp_path / "traces"

    exit_code = main(
        [
            "check",
            str(tmp_path),
            "--skip-pytest-execution",
            "--format",
            "json",
            "--output-dir",
            str(report_output_dir),
            "--checklist-output-dir",
            str(checklist_output_dir),
            "--agent-advice-output-dir",
            str(advice_output_dir),
            "--trace-output-dir",
            str(trace_output_dir),
        ]
    )

    captured = capsys.readouterr()
    terminal_payload = json.loads(captured.out)

    trace_path = trace_output_dir / "trace.json"
    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))

    assert exit_code == EXIT_SUCCESS
    assert captured.err == ""
    assert trace_path.is_file()

    assert terminal_payload["summary"]["blocking"] == 0

    assert trace_payload["tool"] == "releaseguard-agent"
    assert trace_payload["artifact_type"] == "releaseguard_trace"
    assert trace_payload["schema_version"] == "1.0"
    assert trace_payload["project_path"] == str(tmp_path.resolve())
    assert trace_payload["environment"] == {
        "include_pytest_execution": False,
        "output_format": "json",
    }
    assert trace_payload["decision"]["status"] == "ready"
    assert trace_payload["decision"]["release_allowed"] is True
    assert trace_payload["decision"]["blocking_count"] == 0

    assert trace_payload["outputs"]["release_report"] == str(
        report_output_dir.resolve() / "release_report.md"
    )
    assert trace_payload["outputs"]["check_result"] == str(
        report_output_dir.resolve() / "check_result.json"
    )
    assert trace_payload["outputs"]["release_checklist"] == str(
        checklist_output_dir.resolve() / "release_checklist.md"
    )
    assert trace_payload["outputs"]["release_decision_advice_markdown"] == str(
        advice_output_dir.resolve() / "release_decision_advice.md"
    )
    assert trace_payload["outputs"]["release_decision_advice_json"] == str(
        advice_output_dir.resolve() / "release_decision_advice.json"
    )


def test_check_command_agent_advice_reuses_existing_results(
    tmp_path,
    monkeypatch,
    capsys,
):
    calls = {
        "build_runner": 0,
        "run": 0,
    }

    class FakeRunner:
        def run(self, project_path):
            calls["run"] += 1

            return [
                CheckResult(
                    checker_name="fake_checker",
                    status=CheckStatus.PASSED,
                    risk_level=RiskLevel.INFO,
                    title="Fake check passed",
                    message="The fake check passed.",
                    rule_id="RG-DEPS-001",
                    rule_source="Test rule source",
                    file_path=str(project_path),
                )
            ]

    def fake_build_default_python_runner(
        *,
        include_pytest_execution,
    ):
        calls["build_runner"] += 1
        assert include_pytest_execution is False

        return FakeRunner()

    monkeypatch.setattr(
        (
            "releaseguard_agent.services.release_review_service."
            "build_default_python_runner"
        ),
        fake_build_default_python_runner,
    )

    advice_output_dir = tmp_path / "agent-advice"

    exit_code = main(
        [
            "check",
            str(tmp_path),
            "--skip-pytest-execution",
            "--agent-advice-output-dir",
            str(advice_output_dir),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    assert "ReleaseGuard Agent" in captured.out
    assert calls == {
        "build_runner": 1,
        "run": 1,
    }
    assert (
        advice_output_dir / "release_decision_advice.json"
    ).is_file()


def test_check_command_returns_usage_error_when_output_path_is_file(
    tmp_path,
    capsys,
):
    create_release_ready_project(tmp_path)

    output_path = tmp_path / "report-target"
    output_path.write_text(
        "This is a file, not a directory.\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "check",
            str(tmp_path),
            "--skip-pytest-execution",
            "--output-dir",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == EXIT_USAGE_ERROR
    assert captured.out == ""
    assert "Could not write report artifacts" in captured.err
    assert str(output_path.resolve()) in captured.err


def test_check_command_returns_usage_error_when_checklist_output_path_is_file(
    tmp_path,
    capsys,
):
    create_release_ready_project(tmp_path)

    output_path = tmp_path / "checklist-target"
    output_path.write_text(
        "This is a file, not a directory.\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "check",
            str(tmp_path),
            "--skip-pytest-execution",
            "--checklist-output-dir",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == EXIT_USAGE_ERROR
    assert captured.out == ""
    assert "Could not write release checklist artifact" in captured.err
    assert str(output_path.resolve()) in captured.err


def test_check_command_returns_usage_error_when_agent_advice_output_path_is_file(
    tmp_path,
    capsys,
):
    create_release_ready_project(tmp_path)

    output_path = tmp_path / "agent-advice-target"
    output_path.write_text(
        "This is a file, not a directory.\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "check",
            str(tmp_path),
            "--skip-pytest-execution",
            "--agent-advice-output-dir",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == EXIT_USAGE_ERROR
    assert captured.out == ""
    assert "Could not write Agent advice artifacts" in captured.err
    assert str(output_path.resolve()) in captured.err


def test_check_command_returns_usage_error_when_trace_output_path_is_file(
    tmp_path,
    capsys,
):
    create_release_ready_project(tmp_path)

    output_path = tmp_path / "trace-target"
    output_path.write_text(
        "This is a file, not a directory.\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "check",
            str(tmp_path),
            "--skip-pytest-execution",
            "--trace-output-dir",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == EXIT_USAGE_ERROR
    assert captured.out == ""
    assert "Could not write trace artifact" in captured.err
    assert str(output_path.resolve()) in captured.err


def test_check_command_uses_current_directory_when_path_is_omitted(
    tmp_path,
    monkeypatch,
    capsys,
):
    create_release_ready_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = main(["check", "--skip-pytest-execution"])

    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    assert "ReleaseGuard Agent" in captured.out
    assert f"Project: {tmp_path.resolve()}" in captured.out


def test_check_command_returns_usage_error_for_missing_project_path(
    tmp_path,
    capsys,
):
    missing_path = tmp_path / "missing-project"

    exit_code = main(
        ["check", str(missing_path), "--skip-pytest-execution"]
    )

    captured = capsys.readouterr()

    assert exit_code == EXIT_USAGE_ERROR
    assert "Project path does not exist" in captured.err


def test_list_checkers_outputs_default_checker_names(capsys):
    exit_code = main(["list-checkers"])

    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    assert "dependency_checker" in captured.out
    assert "env_example_checker" in captured.out
    assert "test_structure_checker" in captured.out
    assert "pytest_config_checker" in captured.out
    assert "pytest_execution_checker" in captured.out
    assert "fastapi_detector" in captured.out
    assert "flask_detector" in captured.out
    assert "docker_checker" in captured.out
    assert "docker_style_checker" in captured.out


def test_list_checkers_can_skip_pytest_execution_checker(capsys):
    exit_code = main(
        ["list-checkers", "--skip-pytest-execution"]
    )

    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    assert "dependency_checker" in captured.out
    assert "pytest_config_checker" in captured.out
    assert "pytest_execution_checker" not in captured.out
    assert "fastapi_detector" in captured.out
    assert "flask_detector" in captured.out
    assert "docker_checker" in captured.out
    assert "docker_style_checker" in captured.out
