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
    assert blocking["route_history"][-1] == "plan_fixes"
    assert blocking["review"]["summary"]["blocking"] > 0
