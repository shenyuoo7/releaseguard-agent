from collections.abc import Iterable
from pathlib import Path

from releaseguard_agent.agents.release_decision_synthesizer import (
    ReleaseDecision,
    ReleaseDecisionSynthesizer,
)
from releaseguard_agent.models.check_result import CheckResult
from releaseguard_agent.rag.check_result_enricher import CheckResultEnricher


class ReleaseDecisionAgent:
    """Deterministic Agent facade for release decision synthesis."""

    def __init__(
        self,
        *,
        enricher: CheckResultEnricher,
        synthesizer: ReleaseDecisionSynthesizer | None = None,
    ) -> None:
        self._enricher = enricher
        self._synthesizer = synthesizer or ReleaseDecisionSynthesizer()

    @classmethod
    def from_rule_index(
        cls,
        index_path: Path,
        source_directory: Path | None = None,
    ) -> "ReleaseDecisionAgent":
        """Create the deterministic decision Agent from a rule index."""
        return cls(
            enricher=CheckResultEnricher.from_rule_index(
                index_path=index_path,
                source_directory=source_directory,
            )
        )

    def decide(
        self,
        check_results: Iterable[CheckResult],
    ) -> ReleaseDecision:
        """Build one release decision from raw check results."""
        enriched_results = self._enricher.enrich_many(check_results)

        return self._synthesizer.synthesize(enriched_results)
