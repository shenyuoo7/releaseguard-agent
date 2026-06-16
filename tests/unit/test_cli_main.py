import json

from releaseguard_agent.cli.main import (
    EXIT_BLOCKING_ISSUES,
    EXIT_SUCCESS,
    EXIT_USAGE_ERROR,
    main,
)


def create_release_ready_project(project_path):
    requirements_file = project_path / "requirements.txt"
    requirements_file.write_text("pytest\n", encoding="utf-8")

    env_example_file = project_path / ".env.example"
    env_example_file.write_text("APP_ENV=local\n", encoding="utf-8")

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


def test_check_command_runs_default_non_dynamic_checks(tmp_path, capsys):
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


def test_list_checkers_can_skip_pytest_execution_checker(capsys):
    exit_code = main(["list-checkers", "--skip-pytest-execution"])

    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    assert "dependency_checker" in captured.out
    assert "pytest_config_checker" in captured.out
    assert "pytest_execution_checker" not in captured.out