from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_docker_image_is_a_non_root_offline_default_api_demo() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "FROM python:3.11-slim" in dockerfile
    assert "PYTHONPATH=/app/src" in dockerfile
    assert "USER releaseguard" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "releaseguard_agent.api.app:app" in dockerfile
    assert "COPY . " not in dockerfile
    assert ".env" in dockerignore
    assert ".venv" in dockerignore
    assert ".git" in dockerignore


def test_linux_ci_runs_quality_tests_eval_and_container_smoke() -> None:
    workflow_path = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    workflow = yaml.load(
        workflow_path.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )

    assert set(workflow["on"]) == {"push", "pull_request"}
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert set(jobs) == {"quality-and-tests", "container-smoke"}
    assert all(job["runs-on"] == "ubuntu-latest" for job in jobs.values())

    commands = "\n".join(
        step.get("run", "")
        for job in jobs.values()
        for step in job["steps"]
    )
    for required in (
        "ruff check src tests scripts",
        "mypy src/releaseguard_agent",
        "pytest -p no:cacheprovider tests/unit",
        "pytest -p no:cacheprovider tests/integration",
        "pytest -p no:cacheprovider tests/e2e",
        "releaseguard_agent.cli.main evaluate",
        "docker build",
        "docker run",
        "http_health_smoke.py",
    ):
        assert required in commands
