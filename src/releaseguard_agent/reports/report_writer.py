import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from releaseguard_agent.models.check_result import CheckResult


@dataclass(frozen=True)
class ReportArtifacts:
    """Paths of report files written by ReleaseGuard."""

    output_dir: Path
    markdown_path: Path
    json_path: Path


def build_report_payload(
    *,
    project_path: Path,
    include_pytest_execution: bool,
    summary: dict[str, object],
    results: list[CheckResult],
) -> dict[str, Any]:
    """Build a stable machine-readable report payload."""
    return {
        "tool": "releaseguard-agent",
        "project_path": str(project_path),
        "include_pytest_execution": include_pytest_execution,
        "summary": summary,
        "results": [result.to_dict() for result in results],
    }


def render_markdown_report(payload: dict[str, Any]) -> str:
    """Render a human-readable Markdown release report."""
    summary = payload["summary"]
    results = payload["results"]

    lines = [
        "# ReleaseGuard Report",
        "",
        f"- Project: `{payload['project_path']}`",
        f"- Pytest execution: `{_enabled_label(payload['include_pytest_execution'])}`",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "| --- | ---: |",
        f"| Total | {summary['total']} |",
        f"| Passed | {summary['passed']} |",
        f"| Failed | {summary['failed']} |",
        f"| Warning | {summary['warning']} |",
        f"| Skipped | {summary['skipped']} |",
        f"| Blocking | {summary['blocking']} |",
        "",
        "## Results",
        "",
    ]

    for result in results:
        lines.extend(_render_result(result))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report_artifacts(
    *,
    output_dir: Path,
    payload: dict[str, Any],
) -> ReportArtifacts:
    """Write Markdown and JSON report artifacts to an output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = output_dir / "release_report.md"
    json_path = output_dir / "check_result.json"

    markdown_path.write_text(
        render_markdown_report(payload),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    return ReportArtifacts(
        output_dir=output_dir,
        markdown_path=markdown_path,
        json_path=json_path,
    )


def _render_result(result: dict[str, Any]) -> list[str]:
    rule_id = result.get("rule_id") or "NO-RULE"
    lines = [
        f"### [{result['status'].upper()}] {rule_id} - {result['title']}",
        "",
        f"- Checker: `{result['checker_name']}`",
        f"- Risk: `{result['risk_level']}`",
        f"- Blocking: `{result['should_block_release']}`",
        f"- Message: {result['message']}",
    ]

    if result.get("rule_source"):
        lines.append(f"- Rule source: {result['rule_source']}")

    if result.get("file_path"):
        lines.append(f"- File path: `{result['file_path']}`")

    evidence = result.get("evidence") or []
    if evidence:
        lines.append("")
        lines.append("Evidence:")
        for item in evidence:
            lines.append(f"- {item}")

    recommendation = result.get("recommendation")
    if recommendation:
        lines.append("")
        lines.append(f"Recommendation: {recommendation}")

    return lines


def _enabled_label(enabled: bool) -> str:
    if enabled:
        return "enabled"

    return "disabled"