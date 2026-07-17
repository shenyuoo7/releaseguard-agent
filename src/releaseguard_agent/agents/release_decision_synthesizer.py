from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import cast

from releaseguard_agent.models.check_result import (
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.rag.check_result_enricher import (
    EnrichedCheckResult,
)


class ReleaseDecisionStatus(str, Enum):
    """Agent-facing release decision status."""

    READY = "ready"
    REVIEW_RECOMMENDED = "review_recommended"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ReleaseDecision:
    """Deterministic Agent-ready release decision."""

    status: ReleaseDecisionStatus
    release_allowed: bool
    summary: dict[str, object]
    blocking_rule_ids: tuple[str, ...]
    warning_rule_ids: tuple[str, ...]
    missing_rule_evidence_count: int
    source_urls: tuple[str, ...]
    agent_summary: str
    enriched_results: tuple[EnrichedCheckResult, ...]

    def to_dict(self) -> dict[str, object]:
        """Convert the release decision to a plain dictionary."""
        return {
            "status": self.status.value,
            "release_allowed": self.release_allowed,
            "summary": self.summary,
            "blocking_rule_ids": list(self.blocking_rule_ids),
            "warning_rule_ids": list(self.warning_rule_ids),
            "missing_rule_evidence_count": (
                self.missing_rule_evidence_count
            ),
            "source_urls": list(self.source_urls),
            "agent_summary": self.agent_summary,
            "enriched_results": [
                enriched_result.to_dict()
                for enriched_result in self.enriched_results
            ],
        }


class ReleaseDecisionSynthesizer:
    """Build deterministic release decisions from enriched check results."""

    def synthesize(
        self,
        enriched_results: Iterable[EnrichedCheckResult],
    ) -> ReleaseDecision:
        """Summarize enriched check results into one release decision."""
        ordered_results = tuple(enriched_results)
        summary = _build_summary(ordered_results)
        blocking_rule_ids = _collect_rule_ids(
            enriched_result
            for enriched_result in ordered_results
            if enriched_result.check_result.should_block_release
        )
        warning_rule_ids = _collect_rule_ids(
            enriched_result
            for enriched_result in ordered_results
            if enriched_result.check_result.status == CheckStatus.WARNING
        )
        source_urls = _collect_source_urls(ordered_results)
        missing_rule_evidence_count = sum(
            1
            for enriched_result in ordered_results
            if not enriched_result.has_rule_evidence
        )

        release_allowed = not blocking_rule_ids
        status = _determine_status(
            release_allowed=release_allowed,
            warning_count=cast(int, summary["warning"]),
            missing_rule_evidence_count=missing_rule_evidence_count,
        )
        agent_summary = _build_agent_summary(
            status=status,
            blocking_count=cast(int, summary["blocking"]),
            warning_count=cast(int, summary["warning"]),
            missing_rule_evidence_count=missing_rule_evidence_count,
        )

        return ReleaseDecision(
            status=status,
            release_allowed=release_allowed,
            summary=summary,
            blocking_rule_ids=blocking_rule_ids,
            warning_rule_ids=warning_rule_ids,
            missing_rule_evidence_count=missing_rule_evidence_count,
            source_urls=source_urls,
            agent_summary=agent_summary,
            enriched_results=ordered_results,
        )


def _build_summary(
    enriched_results: tuple[EnrichedCheckResult, ...],
) -> dict[str, object]:
    status_counts = {status.value: 0 for status in CheckStatus}
    risk_counts = {risk.value: 0 for risk in RiskLevel}

    for enriched_result in enriched_results:
        check_result = enriched_result.check_result
        status_counts[check_result.status.value] += 1
        risk_counts[check_result.risk_level.value] += 1

    blocking_count = sum(
        1
        for enriched_result in enriched_results
        if enriched_result.check_result.should_block_release
    )

    return {
        "total": len(enriched_results),
        "passed": status_counts[CheckStatus.PASSED.value],
        "failed": status_counts[CheckStatus.FAILED.value],
        "warning": status_counts[CheckStatus.WARNING.value],
        "skipped": status_counts[CheckStatus.SKIPPED.value],
        "blocking": blocking_count,
        "status_counts": status_counts,
        "risk_counts": risk_counts,
    }


def _determine_status(
    *,
    release_allowed: bool,
    warning_count: int,
    missing_rule_evidence_count: int,
) -> ReleaseDecisionStatus:
    if not release_allowed:
        return ReleaseDecisionStatus.BLOCKED

    if warning_count > 0 or missing_rule_evidence_count > 0:
        return ReleaseDecisionStatus.REVIEW_RECOMMENDED

    return ReleaseDecisionStatus.READY


def _build_agent_summary(
    *,
    status: ReleaseDecisionStatus,
    blocking_count: int,
    warning_count: int,
    missing_rule_evidence_count: int,
) -> str:
    if status == ReleaseDecisionStatus.BLOCKED:
        return (
            "Release is blocked by "
            f"{blocking_count} high or critical failed check(s)."
        )

    if status == ReleaseDecisionStatus.REVIEW_RECOMMENDED:
        return (
            "Release is allowed by the current blocking policy, but review is "
            f"recommended for {warning_count} warning check(s) and "
            f"{missing_rule_evidence_count} result(s) without rule evidence."
        )

    return (
        "Release is ready: no blocking or warning checks were found, and all "
        "results have rule evidence."
    )


def _collect_rule_ids(
    enriched_results: Iterable[EnrichedCheckResult],
) -> tuple[str, ...]:
    rule_ids: list[str] = []

    for enriched_result in enriched_results:
        rule_id = (
            enriched_result.rule_evidence.rule_id
            if enriched_result.rule_evidence is not None
            else enriched_result.check_result.rule_id
        )

        if rule_id is not None and rule_id.strip():
            rule_ids.append(rule_id.strip())

    return _unique(rule_ids)


def _collect_source_urls(
    enriched_results: tuple[EnrichedCheckResult, ...],
) -> tuple[str, ...]:
    source_urls: list[str] = []

    for enriched_result in enriched_results:
        if enriched_result.rule_evidence is None:
            continue

        for source_document in enriched_result.rule_evidence.source_documents:
            if source_document.source_url:
                source_urls.append(source_document.source_url)

    return _unique(source_urls)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []

    for value in values:
        if value in seen:
            continue

        seen.add(value)
        ordered.append(value)

    return tuple(ordered)
