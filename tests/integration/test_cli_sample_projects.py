import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PROJECTS = PROJECT_ROOT / "sample_projects"


def run_releaseguard(
    sample_name: str,
    output_dir: Path,
    *,
    skip_pytest_execution: bool = False,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    source_path = str(PROJECT_ROOT / "src")
    existing_pythonpath = environment.get("PYTHONPATH")

    if existing_pythonpath:
        environment["PYTHONPATH"] = (
            source_path + os.pathsep + existing_pythonpath
        )
    else:
        environment["PYTHONPATH"] = source_path

    command = [
        sys.executable,
        "-m",
        "releaseguard_agent.cli.main",
        "check",
        str(SAMPLE_PROJECTS / sample_name),
        "--format",
        "json",
        "--output-dir",
        str(output_dir),
    ]

    if skip_pytest_execution:
        command.append("--skip-pytest-execution")

    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )


def load_file_payload(output_dir: Path) -> dict[str, object]:
    json_path = output_dir / "check_result.json"
    return json.loads(json_path.read_text(encoding="utf-8"))


def test_clean_python_project_passes_end_to_end(tmp_path):
    output_dir = tmp_path / "clean-report"

    completed = run_releaseguard(
        "clean_python_project",
        output_dir,
    )

    assert completed.returncode == 0, completed.stderr

    terminal_payload = json.loads(completed.stdout)
    file_payload = load_file_payload(output_dir)
    markdown = (output_dir / "release_report.md").read_text(
        encoding="utf-8"
    )

    assert terminal_payload == file_payload
    assert file_payload["summary"]["total"] == 11
    assert file_payload["summary"]["skipped"] == 2
    assert file_payload["summary"]["failed"] == 0
    assert file_payload["summary"]["blocking"] == 0
    assert "# ReleaseGuard Report" in markdown
    assert "RG-TEST-005" in markdown
    assert "Pytest run succeeded" in markdown


def test_failed_tests_project_blocks_release_end_to_end(tmp_path):
    output_dir = tmp_path / "failed-report"

    completed = run_releaseguard(
        "failed_tests_project",
        output_dir,
    )

    assert completed.returncode == 1, completed.stderr

    terminal_payload = json.loads(completed.stdout)
    file_payload = load_file_payload(output_dir)
    results = file_payload["results"]

    pytest_run_result = next(
        result
        for result in results
        if result["rule_id"] == "RG-TEST-005"
    )

    assert terminal_payload == file_payload
    assert file_payload["summary"]["total"] == 11
    assert file_payload["summary"]["skipped"] == 2
    assert file_payload["summary"]["failed"] == 1
    assert file_payload["summary"]["blocking"] == 1
    assert pytest_run_result["status"] == "failed"
    assert pytest_run_result["risk_level"] == "critical"
    assert pytest_run_result["should_block_release"] is True
    assert (output_dir / "release_report.md").is_file()


def test_fastapi_good_project_passes_end_to_end(tmp_path):
    output_dir = tmp_path / "fastapi-good-report"

    completed = run_releaseguard(
        "fastapi_good_project",
        output_dir,
        skip_pytest_execution=True,
    )

    assert completed.returncode == 0, completed.stderr

    terminal_payload = json.loads(completed.stdout)
    file_payload = load_file_payload(output_dir)
    results = {
        result["rule_id"]: result
        for result in file_payload["results"]
        if result["rule_id"].startswith("RG-FASTAPI-")
    }

    assert terminal_payload == file_payload
    assert file_payload["summary"]["total"] == 8
    assert file_payload["summary"]["failed"] == 0
    assert file_payload["summary"]["blocking"] == 0
    assert file_payload["summary"]["skipped"] == 0
    assert results["RG-FASTAPI-001"]["status"] == "passed"
    assert results["RG-FASTAPI-002"]["status"] == "passed"
    assert (output_dir / "release_report.md").is_file()


def test_fastapi_bad_project_blocks_missing_dependency(tmp_path):
    output_dir = tmp_path / "fastapi-bad-report"

    completed = run_releaseguard(
        "fastapi_bad_project",
        output_dir,
        skip_pytest_execution=True,
    )

    assert completed.returncode == 1, completed.stderr

    terminal_payload = json.loads(completed.stdout)
    file_payload = load_file_payload(output_dir)
    results = {
        result["rule_id"]: result
        for result in file_payload["results"]
        if result["rule_id"].startswith("RG-FASTAPI-")
    }

    assert terminal_payload == file_payload
    assert file_payload["summary"]["total"] == 8
    assert file_payload["summary"]["failed"] == 1
    assert file_payload["summary"]["blocking"] == 1
    assert file_payload["summary"]["skipped"] == 0

    dependency_result = results["RG-FASTAPI-001"]
    assert dependency_result["status"] == "failed"
    assert dependency_result["risk_level"] == "high"
    assert dependency_result["should_block_release"] is True

    assert results["RG-FASTAPI-002"]["status"] == "passed"
    assert (output_dir / "release_report.md").is_file()
