from pathlib import Path

from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.rag.check_result_enricher import (
    CheckResultEnricher,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RULE_INDEX_PATH = (
    PROJECT_ROOT
    / "knowledge_base"
    / "release_rules"
    / "rule_index.md"
)


def _check_result(
    rule_id: str | None = "RG-DEPS-001",
) -> CheckResult:
    return CheckResult(
        checker_name="dependency_checker",
        status=CheckStatus.PASSED,
        risk_level=RiskLevel.INFO,
        title="Dependency declaration exists",
        message="A dependency declaration file was found.",
        evidence=["requirements.txt"],
        recommendation="Keep dependencies declared explicitly.",
        rule_id=rule_id,
        rule_source="The Twelve-Factor App - Dependencies",
    )


def test_enriches_known_rule_id_with_rule_evidence() -> None:
    enricher = CheckResultEnricher.from_rule_index(RULE_INDEX_PATH)
    check_result = _check_result("RG-DEPS-001")

    enriched = enricher.enrich(check_result)

    assert enriched.check_result is check_result
    assert enriched.has_rule_evidence is True
    assert enriched.missing_rule_reason is None
    assert enriched.rule_evidence is not None
    assert enriched.rule_evidence.rule_id == "RG-DEPS-001"
    assert enriched.rule_evidence.rule_name == (
        "Explicit dependency declaration exists"
    )
    assert enriched.rule_evidence.source_documents[0].source_title == (
        "The Twelve-Factor App"
    )


def test_enriched_result_can_be_converted_to_dict() -> None:
    enricher = CheckResultEnricher.from_rule_index(RULE_INDEX_PATH)
    check_result = _check_result("RG-DEPS-001")

    data = enricher.enrich(check_result).to_dict()

    assert data["has_rule_evidence"] is True
    assert data["missing_rule_reason"] is None
    assert data["check_result"]["rule_id"] == "RG-DEPS-001"
    assert data["rule_evidence"]["rule_id"] == "RG-DEPS-001"
    assert data["rule_evidence"]["source_documents"][0]["source_url"] == (
        "https://12factor.net/"
    )


def test_missing_rule_id_returns_missing_reason_without_crashing() -> None:
    enricher = CheckResultEnricher.from_rule_index(RULE_INDEX_PATH)
    check_result = _check_result(rule_id=None)

    enriched = enricher.enrich(check_result)

    assert enriched.check_result is check_result
    assert enriched.has_rule_evidence is False
    assert enriched.rule_evidence is None
    assert enriched.missing_rule_reason == (
        "Check result does not include a rule_id."
    )


def test_blank_rule_id_returns_missing_reason_without_crashing() -> None:
    enricher = CheckResultEnricher.from_rule_index(RULE_INDEX_PATH)
    check_result = _check_result(rule_id="   ")

    enriched = enricher.enrich(check_result)

    assert enriched.has_rule_evidence is False
    assert enriched.rule_evidence is None
    assert enriched.missing_rule_reason == (
        "Check result does not include a rule_id."
    )


def test_unknown_rule_id_returns_missing_reason_without_crashing() -> None:
    enricher = CheckResultEnricher.from_rule_index(RULE_INDEX_PATH)
    check_result = _check_result("RG-UNKNOWN-999")

    enriched = enricher.enrich(check_result)

    assert enriched.has_rule_evidence is False
    assert enriched.rule_evidence is None
    assert enriched.missing_rule_reason == (
        "Rule ID 'RG-UNKNOWN-999' was not found in the rule index."
    )


def test_enrich_many_preserves_input_order() -> None:
    enricher = CheckResultEnricher.from_rule_index(RULE_INDEX_PATH)
    first = _check_result("RG-DEPS-001")
    second = _check_result("RG-DOCKER-003")
    third = _check_result("RG-UNKNOWN-999")

    enriched_results = enricher.enrich_many((first, second, third))

    assert len(enriched_results) == 3
    assert enriched_results[0].check_result is first
    assert enriched_results[1].check_result is second
    assert enriched_results[2].check_result is third
    assert enriched_results[0].rule_evidence is not None
    assert enriched_results[0].rule_evidence.rule_id == "RG-DEPS-001"
    assert enriched_results[1].rule_evidence is not None
    assert enriched_results[1].rule_evidence.rule_id == "RG-DOCKER-003"
    assert enriched_results[2].rule_evidence is None


def test_enrichment_does_not_mutate_original_check_result() -> None:
    enricher = CheckResultEnricher.from_rule_index(RULE_INDEX_PATH)
    check_result = _check_result("RG-DEPS-001")
    original_data = check_result.to_dict()

    enricher.enrich(check_result)

    assert check_result.to_dict() == original_data
