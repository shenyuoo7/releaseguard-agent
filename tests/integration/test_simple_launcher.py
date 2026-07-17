import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = PROJECT_ROOT / "scripts" / "simple_launcher.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(
    os.name != "nt" or POWERSHELL is None,
    reason="The one-click launcher is a Windows PowerShell entry point.",
)


def _launcher_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in tuple(env):
        if key.upper().startswith("RELEASEGUARD_LLM_"):
            env.pop(key)
    return env


def _run_launcher(*arguments: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(POWERSHELL),
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(LAUNCHER),
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        env=_launcher_env(),
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def _json_result(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, completed.stderr
    return json.loads(lines[-1])


def test_launcher_reports_a_missing_project_without_closing_ambiguously(tmp_path: Path) -> None:
    completed = _run_launcher(
        "-Action",
        "Check",
        "-ProjectPath",
        str(tmp_path / "missing project"),
        "-RuntimeRoot",
        str(tmp_path / "runtime"),
        "-NoPause",
        "-Json",
    )

    assert completed.returncode == 2
    result = _json_result(completed)
    assert result["status"] == "error"
    assert "不存在" in str(result["message"])


def test_launcher_checks_a_project_whose_path_contains_spaces(tmp_path: Path) -> None:
    project = tmp_path / "project with spaces"
    shutil.copytree(PROJECT_ROOT / "sample_projects" / "clean_python_project", project)
    output = tmp_path / "review output"
    completed = _run_launcher(
        "-Action",
        "Check",
        "-ProjectPath",
        f'"{project}"',
        "-OutputRoot",
        str(output),
        "-RuntimeRoot",
        str(tmp_path / "runtime"),
        "-NoPause",
        "-Json",
    )

    assert completed.returncode == 0, completed.stderr
    result = _json_result(completed)
    assert result["release_allowed"] is True
    assert result["blocking_count"] == 0
    assert Path(str(result["report_path"])).is_file()
    assert Path(str(result["checklist_path"])).is_file()
    assert Path(str(result["trace_path"])).is_file()
    assert Path(str(result["runtime_root"])).drive.upper() == "E:"


def test_launcher_reports_a_blocking_sample_as_a_valid_result(tmp_path: Path) -> None:
    completed = _run_launcher(
        "-Action",
        "Check",
        "-ProjectPath",
        str(PROJECT_ROOT / "sample_projects" / "fastapi_bad_project"),
        "-OutputRoot",
        str(tmp_path / "blocking output"),
        "-RuntimeRoot",
        str(tmp_path / "runtime"),
        "-NoPause",
        "-Json",
    )

    assert completed.returncode == 0, completed.stderr
    result = _json_result(completed)
    assert result["release_allowed"] is False
    assert int(result["blocking_count"]) > 0


def test_launcher_verifies_before_and_after_projects(tmp_path: Path) -> None:
    completed = _run_launcher(
        "-Action",
        "Verify",
        "-BeforePath",
        str(PROJECT_ROOT / "sample_projects" / "fastapi_bad_project"),
        "-AfterPath",
        str(PROJECT_ROOT / "sample_projects" / "fastapi_good_project"),
        "-OutputRoot",
        str(tmp_path / "verification output"),
        "-RuntimeRoot",
        str(tmp_path / "runtime"),
        "-NoPause",
        "-Json",
    )

    assert completed.returncode == 0, completed.stderr
    result = _json_result(completed)
    assert int(result["resolved_count"]) > 0
    assert "new_count" in result
    assert "unchanged_count" in result
    assert Path(str(result["result_path"])).is_file()
    assert Path(str(result["trace_path"])).is_file()


def test_launcher_starts_web_health_and_stops_its_test_process(tmp_path: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    completed = _run_launcher(
        "-Action",
        "Web",
        "-Port",
        str(port),
        "-RuntimeRoot",
        str(tmp_path / "runtime"),
        "-NoBrowser",
        "-NoPause",
        "-TestMode",
        "-Json",
    )

    assert completed.returncode == 0, completed.stderr
    result = _json_result(completed)
    assert result["status"] == "started"
    assert result["health"] == "ok"


def test_launcher_menu_exits_normally() -> None:
    completed = _run_launcher("-Action", "Menu", "-NoPause", input_text="0\n")

    assert completed.returncode == 0, completed.stderr
