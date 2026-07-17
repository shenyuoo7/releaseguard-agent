import json
from pathlib import Path

from releaseguard_agent.cli.main import (
    EXIT_BLOCKING_ISSUES,
    EXIT_SUCCESS,
    main,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = PROJECT_ROOT / "sample_projects"


def test_agent_review_cli_executes_clean_and_blocking_graph_paths(capsys) -> None:
    clean_code = main(
        [
            "agent-review",
            str(SAMPLES / "clean_python_project"),
            "--skip-pytest-execution",
        ]
    )
    clean = json.loads(capsys.readouterr().out)
    blocking_code = main(
        [
            "agent-review",
            str(SAMPLES / "fastapi_bad_project"),
            "--skip-pytest-execution",
        ]
    )
    blocking = json.loads(capsys.readouterr().out)

    assert clean_code == EXIT_SUCCESS
    assert clean["route_history"] == ["scan", "finalize_clean"]
    assert blocking_code == EXIT_BLOCKING_ISSUES
    assert blocking["route_history"][-1] == "fix_planner_agent"
    assert blocking["review"]["summary"]["blocking"] > 0


def test_verify_cli_reports_user_applied_fix_delta(tmp_path, capsys) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    _write_project(before, dependency=False)
    _write_project(after, dependency=True)

    exit_code = main(
        [
            "verify",
            str(before),
            str(after),
            "--skip-pytest-execution",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == EXIT_SUCCESS
    assert payload["delta"]["status"] == "resolved"
    assert payload["delta"]["resolved"]
    assert payload["route_history"][-1] == "verification_complete"


def _write_project(path: Path, *, dependency: bool) -> None:
    path.mkdir()
    if dependency:
        (path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (path / ".env.example").write_text("APP_ENV=test\n", encoding="utf-8")
    (path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "def test_sample():\n    assert True\n", encoding="utf-8"
    )
