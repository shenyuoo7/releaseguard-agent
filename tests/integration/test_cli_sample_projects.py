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
    assert file_payload["summary"]["total"] == 23
    assert file_payload["summary"]["skipped"] == 14
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
    assert file_payload["summary"]["total"] == 23
    assert file_payload["summary"]["skipped"] == 14
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
    assert file_payload["summary"]["total"] == 20
    assert file_payload["summary"]["failed"] == 0
    assert file_payload["summary"]["blocking"] == 0
    assert file_payload["summary"]["skipped"] == 12
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
    assert file_payload["summary"]["total"] == 20
    assert file_payload["summary"]["failed"] == 1
    assert file_payload["summary"]["blocking"] == 1
    assert file_payload["summary"]["skipped"] == 12

    dependency_result = results["RG-FASTAPI-001"]
    assert dependency_result["status"] == "failed"
    assert dependency_result["risk_level"] == "high"
    assert dependency_result["should_block_release"] is True

    assert results["RG-FASTAPI-002"]["status"] == "passed"
    assert (output_dir / "release_report.md").is_file()

FLASK_RULE_IDS = {
    "RG-FLASK-001",
    "RG-FLASK-002",
    "RG-FLASK-003",
    "RG-SEC-002",
}


def get_flask_results(payload):
    return {
        result["rule_id"]: result
        for result in payload["results"]
        if result["rule_id"] in FLASK_RULE_IDS
    }


def test_flask_good_project_passes_end_to_end(tmp_path):
    output_dir = tmp_path / "flask-good-report"

    completed = run_releaseguard(
        "flask_good_project",
        output_dir,
        skip_pytest_execution=True,
    )

    assert completed.returncode == 0, completed.stderr

    terminal_payload = json.loads(completed.stdout)
    file_payload = load_file_payload(output_dir)
    results = get_flask_results(file_payload)

    assert terminal_payload == file_payload
    assert file_payload["summary"]["total"] == 20
    assert file_payload["summary"]["passed"] == 10
    assert file_payload["summary"]["skipped"] == 10
    assert file_payload["summary"]["warning"] == 0
    assert file_payload["summary"]["failed"] == 0
    assert file_payload["summary"]["blocking"] == 0

    assert results["RG-FLASK-001"]["status"] == "passed"
    assert results["RG-FLASK-002"]["status"] == "passed"
    assert results["RG-FLASK-003"]["status"] == "passed"
    assert results["RG-SEC-002"]["status"] == "passed"
    assert (output_dir / "release_report.md").is_file()


def test_flask_development_server_warns_end_to_end(tmp_path):
    output_dir = tmp_path / "flask-development-report"

    completed = run_releaseguard(
        "flask_dev_server_project",
        output_dir,
        skip_pytest_execution=True,
    )

    assert completed.returncode == 0, completed.stderr

    terminal_payload = json.loads(completed.stdout)
    file_payload = load_file_payload(output_dir)
    results = get_flask_results(file_payload)

    assert terminal_payload == file_payload
    assert file_payload["summary"]["total"] == 20
    assert file_payload["summary"]["passed"] == 9
    assert file_payload["summary"]["skipped"] == 10
    assert file_payload["summary"]["warning"] == 1
    assert file_payload["summary"]["failed"] == 0
    assert file_payload["summary"]["blocking"] == 0

    server_result = results["RG-FLASK-003"]

    assert server_result["status"] == "warning"
    assert server_result["risk_level"] == "medium"
    assert server_result["should_block_release"] is False
    assert results["RG-SEC-002"]["status"] == "passed"
    assert (output_dir / "release_report.md").is_file()


def test_flask_debug_project_blocks_release_end_to_end(tmp_path):
    output_dir = tmp_path / "flask-debug-report"

    completed = run_releaseguard(
        "flask_bad_project",
        output_dir,
        skip_pytest_execution=True,
    )

    assert completed.returncode == 1, completed.stderr

    terminal_payload = json.loads(completed.stdout)
    file_payload = load_file_payload(output_dir)
    results = get_flask_results(file_payload)

    assert terminal_payload == file_payload
    assert file_payload["summary"]["total"] == 20
    assert file_payload["summary"]["passed"] == 8
    assert file_payload["summary"]["skipped"] == 10
    assert file_payload["summary"]["warning"] == 1
    assert file_payload["summary"]["failed"] == 1
    assert file_payload["summary"]["blocking"] == 1

    debug_result = results["RG-SEC-002"]

    assert results["RG-FLASK-003"]["status"] == "warning"
    assert debug_result["status"] == "failed"
    assert debug_result["risk_level"] == "high"
    assert debug_result["should_block_release"] is True
    assert any(
        "app.run(debug=True)" in evidence
        for evidence in debug_result["evidence"]
    )
    assert (output_dir / "release_report.md").is_file()
