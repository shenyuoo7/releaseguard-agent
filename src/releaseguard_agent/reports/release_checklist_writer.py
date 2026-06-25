import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from releaseguard_agent.models.check_result import CheckResult


CHECKLIST_FILE_NAME = "release_checklist.md"
CHECKLIST_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class ReleaseChecklistArtifacts:
    """Paths of release checklist files written by ReleaseGuard."""

    output_dir: Path
    markdown_path: Path


def build_release_checklist_payload(
    *,
    project_path: Path,
    include_pytest_execution: bool,
    summary: dict[str, object],
    results: list[CheckResult],
) -> dict[str, Any]:
    """Build a stable release checklist payload.

    The checklist groups existing check results into operator-facing sections
    without changing checker behavior or release-blocking policy.
    """
    serialized_results = [
        copy.deepcopy(result.to_dict())
        for result in results
    ]

    return {
        "tool": "releaseguard-agent",
        "artifact_type": "releaseguard_release_checklist",
        "schema_version": CHECKLIST_SCHEMA_VERSION,
        "project_path": str(project_path),
        "include_pytest_execution": include_pytest_execution,
        "summary": copy.deepcopy(summary),
        "sections": {
            "blocking_fixes": [
                result
                for result in serialized_results
                if result["should_block_release"]
            ],
            "warnings_to_review": [
                result
                for result in serialized_results
                if _is_review_item(result)
            ],
            "passed_checks": [
                result
                for result in serialized_results
                if result["status"] == "passed"
            ],
            "skipped_checks": [
                result
                for result in serialized_results
                if result["status"] == "skipped"
            ],
        },
    }


def render_release_checklist_markdown(payload: dict[str, Any]) -> str:
    """Render a human-readable Markdown release checklist."""
    summary = payload["summary"]
    sections = payload["sections"]

    lines = [
        "# ReleaseGuard Release Checklist",
        "",
        f"- Project: `{payload['project_path']}`",
        f"- Pytest execution: `{_enabled_label(payload['include_pytest_execution'])}`",
        f"- Schema version: `{payload['schema_version']}`",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "| --- | ---: |",
        f"| Total | {_summary_count(summary, 'total')} |",
        f"| Passed | {_summary_count(summary, 'passed')} |",
        f"| Failed | {_summary_count(summary, 'failed')} |",
        f"| Warning | {_summary_count(summary, 'warning')} |",
        f"| Skipped | {_summary_count(summary, 'skipped')} |",
        f"| Blocking | {_summary_count(summary, 'blocking')} |",
        "",
    ]

    lines.extend(
        _render_section(
            title="Blocking fixes",
            empty_message="No blocking fixes.",
            items=sections["blocking_fixes"],
            checked=False,
        )
    )
    lines.append("")

    lines.extend(
        _render_section(
            title="Warnings to review",
            empty_message="No warnings to review.",
            items=sections["warnings_to_review"],
            checked=False,
        )
    )
    lines.append("")

    lines.extend(
        _render_section(
            title="Passed checks",
            empty_message="No passed checks.",
            items=sections["passed_checks"],
            checked=True,
        )
    )
    lines.append("")

    lines.extend(
        _render_section(
            title="Skipped checks",
            empty_message="No skipped checks.",
            items=sections["skipped_checks"],
            checked=False,
        )
    )

    return "\n".join(lines).rstrip() + "\n"


def write_release_checklist_artifact(
    *,
    output_dir: Path,
    payload: dict[str, Any],
) -> ReleaseChecklistArtifacts:
    """Write release_checklist.md to an output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = output_dir / CHECKLIST_FILE_NAME
    markdown_path.write_text(
        render_release_checklist_markdown(payload),
        encoding="utf-8",
    )

    return ReleaseChecklistArtifacts(
        output_dir=output_dir,
        markdown_path=markdown_path,
    )


def _is_review_item(result: dict[str, Any]) -> bool:
    if result["should_block_release"]:
        return False

    return result["status"] in {"failed", "warning"}


def _render_section(
    *,
    title: str,
    empty_message: str,
    items: list[dict[str, Any]],
    checked: bool,
) -> list[str]:
    lines = [
        f"## {title}",
        "",
    ]

    if not items:
        lines.append(empty_message)
        return lines

    for index, item in enumerate(items):
        if index > 0:
            lines.append("")

        lines.extend(
            _render_checklist_item(
                item=item,
                checked=checked,
            )
        )

    return lines


def _render_checklist_item(
    *,
    item: dict[str, Any],
    checked: bool,
) -> list[str]:
    checkbox = "[x]" if checked else "[ ]"
    rule_id = item.get("rule_id") or "NO-RULE"

    lines = [
        f"- {checkbox} `{rule_id}` {item['title']}",
        f"  - Checker: `{item['checker_name']}`",
        f"  - Status: `{item['status']}`",
        f"  - Risk: `{item['risk_level']}`",
        f"  - Blocking: `{item['should_block_release']}`",
        f"  - Message: {item['message']}",
    ]

    if item.get("rule_source"):
        lines.append(f"  - Rule source: {item['rule_source']}")

    if item.get("file_path"):
        lines.append(f"  - File path: `{item['file_path']}`")

    evidence = item.get("evidence") or []
    if evidence:
        lines.append("  - Evidence:")
        for evidence_item in evidence:
            lines.append(f"    - {evidence_item}")

    recommendation = item.get("recommendation")
    if recommendation:
        lines.append(f"  - Recommendation: {recommendation}")

    return lines


def _summary_count(
    summary: dict[str, Any],
    key: str,
) -> object:
    return summary.get(key, 0)


def _enabled_label(enabled: bool) -> str:
    if enabled:
        return "enabled"

    return "disabled"
