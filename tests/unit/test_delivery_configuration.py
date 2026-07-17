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


def test_windows_batch_entry_opens_the_simple_launcher() -> None:
    batch = (PROJECT_ROOT / "ReleaseGuard.bat").read_text(encoding="utf-8")

    assert "%~dp0" in batch
    assert "scripts\\simple_launcher.ps1" in batch
    assert "powershell.exe" in batch
    assert "ExecutionPolicy Bypass" in batch
    assert "pause" in batch
    assert "set \"RELEASEGUARD_EXIT_CODE=%ERRORLEVEL%\"" in batch
    assert "exit /b %RELEASEGUARD_EXIT_CODE%" in batch
    assert "RELEASEGUARD_LLM" not in batch


def test_simple_launcher_has_only_the_approved_main_menu() -> None:
    launcher = (PROJECT_ROOT / "scripts" / "simple_launcher.ps1").read_text(
        encoding="utf-8"
    )

    for option in (
        "1. 快速检查一个项目",
        "2. 启动网页界面",
        "3. 对比修复前后项目",
        "4. 运行自带演示",
        "5. 安装或修复运行环境",
        "0. 退出",
    ):
        assert launcher.count(option) == 1


def test_simple_launcher_keeps_default_operation_offline_and_on_e_drive() -> None:
    launcher = (PROJECT_ROOT / "scripts" / "simple_launcher.ps1").read_text(
        encoding="utf-8"
    )

    assert "--skip-pytest-execution" in launcher
    assert "outputs\\latest_review" in launcher
    assert "outputs\\latest_verification" in launcher
    assert "127.0.0.1" in launcher
    assert "0.0.0.0" not in launcher
    assert "$env:TEMP" in launcher
    assert "$env:TMP" in launcher
    assert "$env:TMPDIR" in launcher
    assert "$env:PIP_CACHE_DIR" in launcher
    assert "$env:PYTEST_ADDOPTS" in launcher
    assert "Join-Path $script:ProjectRoot '.runtime'" in launcher
    assert "正在检查" in launcher
    assert "确定性离线模式" in launcher
    assert "真实 LLM 智能分析需要 API Key" in launcher
    assert "RELEASEGUARD_LLM" not in launcher
    assert "Get-Content .env" not in launcher
