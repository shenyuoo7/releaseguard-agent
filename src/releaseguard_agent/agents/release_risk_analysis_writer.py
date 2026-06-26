import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from releaseguard_agent.agents.release_risk_analysis_agent import (
    ReleaseRiskAnalysisResult,
)


RELEASE_RISK_ANALYSIS_ARTIFACT_SCHEMA_VERSION = "1.0"
RISK_ANALYSIS_MARKDOWN_FILE_NAME = "agent_risk_analysis.md"
RISK_ANALYSIS_DECISION_FILE_NAME = "agent_decision.json"
FIX_PLAN_MARKDOWN_FILE_NAME = "agent_fix_plan.md"


@dataclass(frozen=True)
class ReleaseRiskAnalysisArtifacts:
    """Paths of LLM Agent risk-analysis artifacts."""

    output_dir: Path
    risk_analysis_markdown_path: Path
    decision_json_path: Path
    fix_plan_markdown_path: Path


def build_release_risk_analysis_payload(
    *,
    result: ReleaseRiskAnalysisResult,
) -> dict[str, Any]:
    """Build a stable machine-readable LLM risk-analysis payload."""
    result_data = result.to_dict()
    analysis = copy.deepcopy(result_data["analysis"])

    return {
        "tool": "releaseguard-agent",
        "artifact_type": "release-risk-analysis",
        "schema_version": RELEASE_RISK_ANALYSIS_ARTIFACT_SCHEMA_VERSION,
        "project_path": str(result.context.advice_result.project_path),
        "decision": {
            "release_status": analysis["release_status"],
            "release_allowed": analysis["release_allowed"],
            "risk_level": analysis["risk_level"],
            "model_release_status": analysis["model_release_status"],
            "model_release_allowed": analysis["model_release_allowed"],
        },
        "analysis": analysis,
        "context": copy.deepcopy(result_data["context"]),
        "llm_response": copy.deepcopy(result_data["llm_response"]),
        "prompt_messages": copy.deepcopy(result_data["prompt_messages"]),
    }


def render_release_risk_analysis_markdown(
    payload: dict[str, Any],
) -> str:
    """Render a human-readable LLM release-risk analysis artifact."""
    analysis = payload["analysis"]
    decision = payload["decision"]

    lines = [
        "# ReleaseGuard Agent Risk Analysis",
        "",
        f"- Project: `{payload['project_path']}`",
        f"- Deterministic status: `{decision['release_status']}`",
        f"- Release allowed: `{_yes_no(decision['release_allowed'])}`",
        f"- Risk level: `{decision['risk_level']}`",
        f"- Schema version: `{payload['schema_version']}`",
        "",
        "## Summary",
        "",
        analysis["summary"],
        "",
    ]

    lines.extend(
        _render_risk_section(
            items=analysis["prioritized_risks"],
        )
    )
    lines.append("")

    lines.extend(
        _render_fix_section(
            items=analysis["fix_plan"],
        )
    )
    lines.append("")

    lines.extend(
        _render_string_section(
            title="Evidence Rule IDs",
            empty_message="No evidence rule IDs cited.",
            items=analysis["evidence_rule_ids"],
        )
    )
    lines.append("")

    lines.extend(
        _render_string_section(
            title="Guardrail Notes",
            empty_message="No guardrail notes.",
            items=analysis["guardrail_notes"],
        )
    )
    lines.append("")

    lines.extend(
        _render_string_section(
            title="Unsupported Claims",
            empty_message="No unsupported claims.",
            items=analysis["unsupported_claims"],
        )
    )
    lines.append("")

    lines.extend(
        _render_string_section(
            title="Missing Evidence Notes",
            empty_message="No missing evidence notes.",
            items=analysis["missing_evidence_notes"],
        )
    )

    return "\n".join(lines).rstrip() + "\n"


def render_release_fix_plan_markdown(
    payload: dict[str, Any],
) -> str:
    """Render a focused LLM fix-plan artifact."""
    analysis = payload["analysis"]
    decision = payload["decision"]

    lines = [
        "# ReleaseGuard Agent Fix Plan",
        "",
        f"- Project: `{payload['project_path']}`",
        f"- Deterministic status: `{decision['release_status']}`",
        f"- Release allowed: `{_yes_no(decision['release_allowed'])}`",
        f"- Risk level: `{decision['risk_level']}`",
        "",
    ]

    lines.extend(
        _render_fix_section(
            items=analysis["fix_plan"],
        )
    )
    lines.append("")

    lines.extend(
        _render_string_section(
            title="Evidence Rule IDs",
            empty_message="No evidence rule IDs cited.",
            items=analysis["evidence_rule_ids"],
        )
    )
    lines.append("")

    lines.extend(
        _render_string_section(
            title="Guardrail Notes",
            empty_message="No guardrail notes.",
            items=analysis["guardrail_notes"],
        )
    )

    return "\n".join(lines).rstrip() + "\n"


def write_release_risk_analysis_artifacts(
    *,
    output_dir: Path,
    result: ReleaseRiskAnalysisResult,
) -> ReleaseRiskAnalysisArtifacts:
    """Write LLM Agent release-risk analysis artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = build_release_risk_analysis_payload(result=result)
    risk_analysis_markdown_path = (
        output_dir / RISK_ANALYSIS_MARKDOWN_FILE_NAME
    )
    decision_json_path = output_dir / RISK_ANALYSIS_DECISION_FILE_NAME
    fix_plan_markdown_path = output_dir / FIX_PLAN_MARKDOWN_FILE_NAME

    risk_analysis_markdown_path.write_text(
        render_release_risk_analysis_markdown(payload),
        encoding="utf-8",
    )
    decision_json_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    fix_plan_markdown_path.write_text(
        render_release_fix_plan_markdown(payload),
        encoding="utf-8",
    )

    return ReleaseRiskAnalysisArtifacts(
        output_dir=output_dir,
        risk_analysis_markdown_path=risk_analysis_markdown_path,
        decision_json_path=decision_json_path,
        fix_plan_markdown_path=fix_plan_markdown_path,
    )


def _render_risk_section(
    *,
    items: list[dict[str, Any]],
) -> list[str]:
    lines = [
        "## Prioritized Risks",
        "",
    ]

    if not items:
        lines.append("No prioritized risks.")
        return lines

    for index, item in enumerate(items, start=1):
        if index > 1:
            lines.append("")

        rule_id = item.get("rule_id") or "NO-RULE"
        title = item.get("title", "Untitled risk")
        severity = item.get("severity", "unknown")
        reason = item.get("reason", "")
        evidence = item.get("evidence") or []

        lines.extend(
            [
                f"{index}. `{rule_id}` {title}",
                f"   - Severity: `{severity}`",
            ]
        )

        if reason:
            lines.append(f"   - Reason: {reason}")

        if evidence:
            lines.append("   - Evidence:")
            for evidence_item in evidence:
                lines.append(f"     - {evidence_item}")

    return lines


def _render_fix_section(
    *,
    items: list[dict[str, Any]],
) -> list[str]:
    lines = [
        "## Fix Plan",
        "",
    ]

    if not items:
        lines.append("No fix steps.")
        return lines

    for index, item in enumerate(items, start=1):
        if index > 1:
            lines.append("")

        priority = item.get("priority", index)
        title = item.get("title", "Untitled fix")
        action = item.get("action", "")
        validation = item.get("validation", "")
        rule_ids = item.get("rule_ids") or []

        lines.extend(
            [
                f"{priority}. {title}",
            ]
        )

        if action:
            lines.append(f"   - Action: {action}")

        if rule_ids:
            lines.append(
                "   - Rule IDs: "
                + ", ".join(f"`{rule_id}`" for rule_id in rule_ids)
            )

        if validation:
            lines.append(f"   - Validation: {validation}")

    return lines


def _render_string_section(
    *,
    title: str,
    empty_message: str,
    items: list[str],
) -> list[str]:
    lines = [
        f"## {title}",
        "",
    ]

    if not items:
        lines.append(empty_message)
        return lines

    for item in items:
        lines.append(f"- {item}")

    return lines


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
