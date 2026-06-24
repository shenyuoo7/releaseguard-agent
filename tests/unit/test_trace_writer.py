import json
from pathlib import Path

from releaseguard_agent.observability.trace_writer import (
    TRACE_FILE_NAME,
    TRACE_SCHEMA_VERSION,
    build_trace_payload,
    write_trace_artifact,
)


def test_build_trace_payload_uses_stable_trace_contract(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "sample_project"
    report_path = tmp_path / "outputs" / "release_report.md"

    payload = build_trace_payload(
        run_id="run-001",
        created_at="2026-06-24T00:00:00Z",
        project_path=project_path,
        command_args=[
            "releaseguard",
            "check",
            ".",
            "--agent-advice-output-dir",
            "outputs",
        ],
        environment_summary={
            "python_version": "3.11.9",
            "platform": "win32",
        },
        input_artifacts={
            "rule_index": "knowledge_base/release_rules/rule_index.md",
        },
        output_artifacts={
            "release_report": report_path,
        },
        decision_summary={
            "status": "ready",
            "release_allowed": True,
            "blocking_count": 0,
        },
    )

    assert payload == {
        "tool": "releaseguard-agent",
        "artifact_type": "releaseguard_trace",
        "schema_version": TRACE_SCHEMA_VERSION,
        "run_id": "run-001",
        "created_at": "2026-06-24T00:00:00Z",
        "project_path": str(project_path),
        "command": {
            "args": [
                "releaseguard",
                "check",
                ".",
                "--agent-advice-output-dir",
                "outputs",
            ],
        },
        "environment": {
            "python_version": "3.11.9",
            "platform": "win32",
        },
        "inputs": {
            "rule_index": "knowledge_base/release_rules/rule_index.md",
        },
        "outputs": {
            "release_report": str(report_path),
        },
        "decision": {
            "status": "ready",
            "release_allowed": True,
            "blocking_count": 0,
        },
    }


def test_build_trace_payload_copies_mutable_inputs(
    tmp_path: Path,
) -> None:
    environment_summary = {
        "python_version": "3.11.9",
    }
    decision_summary = {
        "status": "blocked",
        "blocking_rule_ids": ["RG-TEST-005"],
    }

    payload = build_trace_payload(
        run_id="run-002",
        created_at="2026-06-24T00:00:00Z",
        project_path=tmp_path,
        command_args=["releaseguard", "check", "."],
        environment_summary=environment_summary,
        decision_summary=decision_summary,
    )

    environment_summary["python_version"] = "changed"
    decision_summary["blocking_rule_ids"].append("RG-DEPS-001")

    assert payload["environment"] == {
        "python_version": "3.11.9",
    }
    assert payload["decision"] == {
        "status": "blocked",
        "blocking_rule_ids": ["RG-TEST-005"],
    }


def test_write_trace_artifact_creates_trace_json(
    tmp_path: Path,
) -> None:
    payload = build_trace_payload(
        run_id="run-003",
        created_at="2026-06-24T00:00:00Z",
        project_path=tmp_path / "project",
        command_args=["releaseguard", "check", "."],
    )

    artifacts = write_trace_artifact(
        output_dir=tmp_path / "trace_output",
        payload=payload,
    )

    assert artifacts.output_dir == tmp_path / "trace_output"
    assert artifacts.trace_path == artifacts.output_dir / TRACE_FILE_NAME
    assert artifacts.trace_path.is_file()

    written_payload = json.loads(
        artifacts.trace_path.read_text(encoding="utf-8")
    )

    assert written_payload == payload
