from pathlib import Path

from releaseguard_agent.agents import (
    ReleaseDecisionAgent,
    ReleaseDecisionStatus,
)
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.rag import (
    CheckResultEnricher,
    RuleIndexRetriever,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RULE_INDEX_PATH = (
    PROJECT_ROOT
    / "knowledge_base"
    / "release_rules"
    / "rule_index.md"
)


def _passed_check_result(rule_id: str = "RG-DEPS-001") -> CheckResult:
    return CheckResult(
        checker_name="example_checker",
        status=CheckStatus.PASSED,
        risk_level=RiskLevel.INFO,
        title="Example release check",
        message="Example check passed.",
        evidence=["Example evidence."],
        recommendation="Keep this release check passing.",
        rule_id=rule_id,
        rule_source="Example source.",
    )


def test_public_rag_api_can_enrich_check_result_with_rule_evidence() -> None:
    retriever = RuleIndexRetriever.from_file(RULE_INDEX_PATH)
    enricher = CheckResultEnricher(retriever)
    check_result = _passed_check_result()

    enriched_result = enricher.enrich(check_result)

    assert enriched_result.check_result is check_result
    assert enriched_result.has_rule_evidence is True
    assert enriched_result.rule_evidence is not None
    assert enriched_result.rule_evidence.rule_id == "RG-DEPS-001"
    assert enriched_result.missing_rule_reason is None


def test_public_agent_and_rag_apis_work_together_for_ready_decision() -> None:
    retriever = RuleIndexRetriever.from_file(RULE_INDEX_PATH)
    enricher = CheckResultEnricher(retriever)
    agent = ReleaseDecisionAgent(enricher=enricher)

    decision = agent.decide(
        (
            _passed_check_result("RG-DEPS-001"),
        )
    )

    assert decision.status == ReleaseDecisionStatus.READY
    assert decision.release_allowed is True
    assert decision.summary["total"] == 1
    assert decision.summary["blocking"] == 0
    assert decision.enriched_results[0].rule_evidence is not None
    assert decision.enriched_results[0].rule_evidence.rule_id == (
        "RG-DEPS-001"
    )
