from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from releaseguard_agent.models.check_result import CheckResult
from releaseguard_agent.models.rule_evidence import RuleEvidence
from releaseguard_agent.rag.rule_index_retriever import RuleIndexRetriever


@dataclass(frozen=True)
class EnrichedCheckResult:
    """A check result with optional rule evidence attached."""

    check_result: CheckResult
    rule_evidence: RuleEvidence | None
    missing_rule_reason: str | None = None

    @property
    def has_rule_evidence(self) -> bool:
        """Return True when this result has matching rule evidence."""
        return self.rule_evidence is not None

    def to_dict(self) -> dict[str, object]:
        """Convert the enriched result to a plain dictionary."""
        return {
            "check_result": self.check_result.to_dict(),
            "rule_evidence": (
                self.rule_evidence.to_dict()
                if self.rule_evidence is not None
                else None
            ),
            "has_rule_evidence": self.has_rule_evidence,
            "missing_rule_reason": self.missing_rule_reason,
        }


class CheckResultEnricher:
    """Attach deterministic rule evidence to checker results."""

    def __init__(self, rule_retriever: RuleIndexRetriever) -> None:
        self._rule_retriever = rule_retriever

    @classmethod
    def from_rule_index(
        cls,
        index_path: Path,
        source_directory: Path | None = None,
    ) -> "CheckResultEnricher":
        """Create an enricher from a rule index file."""
        return cls(
            RuleIndexRetriever.from_file(
                index_path=index_path,
                source_directory=source_directory,
            )
        )

    def enrich(self, check_result: CheckResult) -> EnrichedCheckResult:
        """Attach rule evidence to one check result when possible."""
        rule_id = check_result.rule_id

        if rule_id is None or not rule_id.strip():
            return EnrichedCheckResult(
                check_result=check_result,
                rule_evidence=None,
                missing_rule_reason=(
                    "Check result does not include a rule_id."
                ),
            )

        normalized_rule_id = rule_id.strip()
        rule_evidence = self._rule_retriever.get(normalized_rule_id)

        if rule_evidence is None:
            return EnrichedCheckResult(
                check_result=check_result,
                rule_evidence=None,
                missing_rule_reason=(
                    f"Rule ID {normalized_rule_id!r} was not found "
                    "in the rule index."
                ),
            )

        return EnrichedCheckResult(
            check_result=check_result,
            rule_evidence=rule_evidence,
        )

    def enrich_many(
        self,
        check_results: Iterable[CheckResult],
    ) -> tuple[EnrichedCheckResult, ...]:
        """Attach rule evidence to many check results in input order."""
        return tuple(
            self.enrich(check_result)
            for check_result in check_results
        )
