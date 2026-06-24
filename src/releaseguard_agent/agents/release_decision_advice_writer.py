import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from releaseguard_agent.agents.release_decision_advisor import (
    ReleaseDecisionAdviceResult,
)


ADVICE_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class ReleaseDecisionAdviceArtifacts:
    """Paths of Agent advice artifacts written by ReleaseGuard."""

    output_dir: Path
    markdown_path: Path
    json_path: Path


def build_advice_payload(
    *,
    advice_result: ReleaseDecisionAdviceResult,
) -> dict[str, Any]:
    """Build a stable machine-readable Agent advice payload."""
    return {
        "tool": "releaseguard-agent",
        "artifact_type": "release-decision-advice",
        "schema_version": ADVICE_SCHEMA_VERSION,
        "project_path": str(advice_result.project_path),
        "workflow_result": advice_result.workflow_result.to_dict(),
        "explanation": advice_result.explanation.to_dict(),
    }


def render_advice_markdown(payload: dict[str, Any]) -> str:
    """Render a human-readable Markdown Agent advice artifact."""
    explanation = payload["explanation"]

    lines = [
        "# ReleaseGuard Agent Advice",
        "",
        f"- Project: `{payload['project_path']}`",
        f"- Status: `{explanation['status']}`",
        f"- Release allowed: `{_yes_no(explanation['release_allowed'])}`",
        "",
        explanation["markdown"].rstrip(),
        "",
    ]

    return "\n".join(lines).rstrip() + "\n"


def write_advice_artifacts(
    *,
    output_dir: Path,
    advice_result: ReleaseDecisionAdviceResult,
) -> ReleaseDecisionAdviceArtifacts:
    """Write Markdown and JSON Agent advice artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = build_advice_payload(advice_result=advice_result)
    markdown_path = output_dir / "release_decision_advice.md"
    json_path = output_dir / "release_decision_advice.json"

    markdown_path.write_text(
        render_advice_markdown(payload),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    return ReleaseDecisionAdviceArtifacts(
        output_dir=output_dir,
        markdown_path=markdown_path,
        json_path=json_path,
    )


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
