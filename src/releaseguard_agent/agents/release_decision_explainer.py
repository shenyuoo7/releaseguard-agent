from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass

from releaseguard_agent.agents.release_decision_synthesizer import (
    ReleaseDecision,
    ReleaseDecisionStatus,
)
from releaseguard_agent.models.check_result import CheckStatus
from releaseguard_agent.rag.check_result_enricher import EnrichedCheckResult


@dataclass(frozen=True)
class ReleaseDecisionFinding:
    """Agent-readable explanation for one check result."""

    rule_id: str | None
    title: str
    status: str
    risk_level: str
    message: str
    recommendation: str | None
    rule_name: str | None
    source_titles: tuple[str, ...]
    source_urls: tuple[str, ...]
    missing_rule_reason: str | None

    def to_dict(self) -> dict[str, object]:
        """Convert the finding to a plain dictionary."""
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "status": self.status,
            "risk_level": self.risk_level,
            "message": self.message,
            "recommendation": self.recommendation,
            "rule_name": self.rule_name,
            "source_titles": list(self.source_titles),
            "source_urls": list(self.source_urls),
            "missing_rule_reason": self.missing_rule_reason,
        }


@dataclass(frozen=True)
class ReleaseDecisionExplanation:
    """Structured and Markdown release decision explanation."""

    status: ReleaseDecisionStatus
    release_allowed: bool
    headline: str
    summary: dict[str, object]
    blocking_findings: tuple[ReleaseDecisionFinding, ...]
    warning_findings: tuple[ReleaseDecisionFinding, ...]
    missing_evidence_findings: tuple[ReleaseDecisionFinding, ...]
    source_urls: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Convert the explanation to a plain dictionary."""
        return {
            "status": self.status.value,
            "release_allowed": self.release_allowed,
            "headline": self.headline,
            "summary": deepcopy(self.summary),
            "blocking_findings": [
                finding.to_dict()
                for finding in self.blocking_findings
            ],
            "warning_findings": [
                finding.to_dict()
                for finding in self.warning_findings
            ],
            "missing_evidence_findings": [
                finding.to_dict()
                for finding in self.missing_evidence_findings
            ],
            "source_urls": list(self.source_urls),
            "markdown": self.to_markdown(),
        }

    def to_markdown(self) -> str:
        """Render the explanation as concise Markdown."""
        lines = [
            "# Release Decision",
            "",
            f"Status: {self.status.value}",
            f"Release allowed: {_yes_no(self.release_allowed)}",
            "",
            self.headline,
            "",
        ]

        _append_findings(
            lines=lines,
            section_title="Blocking findings",
            findings=self.blocking_findings,
        )
        _append_findings(
            lines=lines,
            section_title="Warning findings",
            findings=self.warning_findings,
        )
        _append_findings(
            lines=lines,
            section_title="Missing rule evidence",
            findings=self.missing_evidence_findings,
        )
        _append_source_urls(lines, self.source_urls)

        return "\n".join(lines).rstrip() + "\n"


class ReleaseDecisionExplainer:
    """Build deterministic Agent-readable release explanations."""

    def explain(
        self,
        decision: ReleaseDecision,
    ) -> ReleaseDecisionExplanation:
        """Convert one release decision into a structured explanation."""
        blocking_findings = tuple(
            _build_finding(enriched_result)
            for enriched_result in decision.enriched_results
            if enriched_result.check_result.should_block_release
        )
        warning_findings = tuple(
            _build_finding(enriched_result)
            for enriched_result in decision.enriched_results
            if enriched_result.check_result.status == CheckStatus.WARNING
        )
        missing_evidence_findings = tuple(
            _build_finding(enriched_result)
            for enriched_result in decision.enriched_results
            if not enriched_result.has_rule_evidence
        )

        return ReleaseDecisionExplanation(
            status=decision.status,
            release_allowed=decision.release_allowed,
            headline=_build_headline(decision),
            summary=deepcopy(decision.summary),
            blocking_findings=blocking_findings,
            warning_findings=warning_findings,
            missing_evidence_findings=missing_evidence_findings,
            source_urls=decision.source_urls,
        )


def _build_headline(decision: ReleaseDecision) -> str:
    if decision.status == ReleaseDecisionStatus.BLOCKED:
        return (
            "Release blocked: "
            f"{decision.summary['blocking']} blocking check(s) require action."
        )

    if decision.status == ReleaseDecisionStatus.REVIEW_RECOMMENDED:
        return (
            "Release allowed by the blocking policy, but review is "
            f"recommended for {decision.summary['warning']} warning check(s) "
            f"and {decision.missing_rule_evidence_count} result(s) without "
            "rule evidence."
        )

    return (
        "Release ready: no blocking or warning checks were found, and all "
        "results have rule evidence."
    )


def _build_finding(
    enriched_result: EnrichedCheckResult,
) -> ReleaseDecisionFinding:
    check_result = enriched_result.check_result
    rule_evidence = enriched_result.rule_evidence
    source_documents = (
        rule_evidence.source_documents
        if rule_evidence is not None
        else ()
    )

    return ReleaseDecisionFinding(
        rule_id=_rule_id_for(enriched_result),
        title=check_result.title,
        status=check_result.status.value,
        risk_level=check_result.risk_level.value,
        message=check_result.message,
        recommendation=check_result.recommendation,
        rule_name=(
            rule_evidence.rule_name
            if rule_evidence is not None
            else None
        ),
        source_titles=_unique(
            document.source_title
            for document in source_documents
        ),
        source_urls=_unique(
            document.source_url
            for document in source_documents
        ),
        missing_rule_reason=enriched_result.missing_rule_reason,
    )


def _rule_id_for(
    enriched_result: EnrichedCheckResult,
) -> str | None:
    if enriched_result.rule_evidence is not None:
        return enriched_result.rule_evidence.rule_id

    rule_id = enriched_result.check_result.rule_id
    if rule_id is None or not rule_id.strip():
        return None

    return rule_id.strip()


def _append_findings(
    *,
    lines: list[str],
    section_title: str,
    findings: tuple[ReleaseDecisionFinding, ...],
) -> None:
    lines.extend((f"## {section_title}", ""))

    if not findings:
        lines.extend(("None.", ""))
        return

    for finding in findings:
        lines.extend(_finding_markdown_lines(finding))

    lines.append("")


def _finding_markdown_lines(
    finding: ReleaseDecisionFinding,
) -> list[str]:
    lines = [
        f"- {finding.title}",
        f"  - Rule: {finding.rule_id or 'unmapped'}",
        f"  - Status/risk: {finding.status}/{finding.risk_level}",
        f"  - Message: {finding.message}",
    ]

    if finding.rule_name:
        lines.append(f"  - Rule name: {finding.rule_name}")

    if finding.recommendation:
        lines.append(f"  - Recommendation: {finding.recommendation}")

    if finding.missing_rule_reason:
        lines.append(
            f"  - Missing evidence: {finding.missing_rule_reason}"
        )

    if finding.source_titles:
        lines.append(
            "  - Sources: " + ", ".join(finding.source_titles)
        )

    return lines


def _append_source_urls(
    lines: list[str],
    source_urls: tuple[str, ...],
) -> None:
    lines.extend(("## Source URLs", ""))

    if not source_urls:
        lines.extend(("None.", ""))
        return

    for source_url in source_urls:
        lines.append(f"- {source_url}")

    lines.append("")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []

    for value in values:
        if value in seen:
            continue

        seen.add(value)
        ordered.append(value)

    return tuple(ordered)
