import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TRACE_FILE_NAME = "trace.json"
TRACE_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class TraceArtifacts:
    """Paths of observability trace files written by ReleaseGuard."""

    output_dir: Path
    trace_path: Path


def build_trace_payload(
    *,
    run_id: str,
    created_at: str,
    project_path: Path,
    command_args: list[str],
    environment_summary: dict[str, Any] | None = None,
    input_artifacts: dict[str, Path | str] | None = None,
    output_artifacts: dict[str, Path | str] | None = None,
    decision_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable machine-readable trace payload.

    This function does not write files. It prepares deterministic trace data
    for future CLI, API, eval, and observability usage.
    """
    return {
        "tool": "releaseguard-agent",
        "artifact_type": "releaseguard_trace",
        "schema_version": TRACE_SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": created_at,
        "project_path": str(project_path),
        "command": {
            "args": list(command_args),
        },
        "environment": copy.deepcopy(environment_summary or {}),
        "inputs": _stringify_artifacts(input_artifacts or {}),
        "outputs": _stringify_artifacts(output_artifacts or {}),
        "decision": copy.deepcopy(decision_summary),
    }


def write_trace_artifact(
    *,
    output_dir: Path,
    payload: dict[str, Any],
) -> TraceArtifacts:
    """Write trace.json to an output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    trace_path = output_dir / TRACE_FILE_NAME
    trace_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    return TraceArtifacts(
        output_dir=output_dir,
        trace_path=trace_path,
    )


def _stringify_artifacts(
    artifacts: dict[str, Path | str],
) -> dict[str, str]:
    return {
        name: str(path)
        for name, path in artifacts.items()
    }
