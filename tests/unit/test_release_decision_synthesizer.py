from pathlib import Path

from releaseguard_agent.agents.release_decision_synthesizer import (
    ReleaseDecisionStatus,
    ReleaseDecisionSynthesizer,
)
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
    *,
    rule_id: str | None,
    status: CheckStatus = CheckStatus.PASSED,
    risk_level: RiskLevel = RiskLevel.INFO,
    title: str = "Example check",
) -> CheckResult:
    return CheckResult(
        checker_name="example_checker",
        status=status,
        risk_level=risk_level,
        title=title,
        message="Example message.",
        evidence=["Example evidence."],
        recommendation="Example recommendation.",
        rule_id=rule_id,
        rule_source="Example source.",
    )


def _enricher() -> CheckResultEnricher:
    return CheckResultEnricher.from_rule_index(RULE_INDEX_PATH)


def test_synthesizes_blocked_release_decision() -> None:
    enricher = _enricher()
    enriched_results = enricher.enrich_many(
        (
            _check_result(
                rule_id="RG-DEPS-001",
                status=CheckStatus.PASSED,
                risk_level=RiskLevel.INFO,
            ),
            _check_result(
                rule_id="RG-DOCKER-003",
                status=CheckStatus.FAILED,
                risk_level=RiskLevel.HIGH,
                title="Invalid Dockerfile instruction order",
            ),
        )
    )

    decision = ReleaseDecisionSynthesizer().synthesize(enriched_results)

    assert decision.status == ReleaseDecisionStatus.BLOCKED
    assert decision.release_allowed is False
    assert decision.summary["total"] == 2
    assert decision.summary["blocking"] == 1
    assert decision.blocking_rule_ids == ("RG-DOCKER-003",)
    assert decision.warning_rule_ids == ()
    assert "https://docs.docker.com/reference/dockerfile/" in (
        decision.source_urls
    )
    assert decision.agent_summary == (
        "Release is blocked by 1 high or critical failed check(s)."
    )


def test_synthesizes_ready_release_decision() -> None:
    enricher = _enricher()
    enriched_results = enricher.enrich_many(
        (
            _check_result(rule_id="RG-DEPS-001"),
            _check_result(rule_id="RG-TEST-002"),
        )
    )

    decision = ReleaseDecisionSynthesizer().synthesize(enriched_results)

    assert decision.status == ReleaseDecisionStatus.READY
    assert decision.release_allowed is True
    assert decision.summary["passed"] == 2
    assert decision.summary["blocking"] == 0
    assert decision.missing_rule_evidence_count == 0
    assert decision.agent_summary == (
        "Release is ready: no blocking or warning checks were found, and all "
        "results have rule evidence."
    )


def test_synthesizes_review_recommended_for_warning() -> None:
    enricher = _enricher()
    enriched_results = enricher.enrich_many(
        (
            _check_result(
                rule_id="RG-TEST-001",
                status=CheckStatus.WARNING,
                risk_level=RiskLevel.MEDIUM,
                title="Tests directory missing",
            ),
        )
    )

    decision = ReleaseDecisionSynthesizer().synthesize(enriched_results)

    assert decision.status == ReleaseDecisionStatus.REVIEW_RECOMMENDED
    assert decision.release_allowed is True
    assert decision.summary["warning"] == 1
    assert decision.warning_rule_ids == ("RG-TEST-001",)
    assert decision.blocking_rule_ids == ()


def test_synthesizes_review_recommended_for_missing_rule_evidence() -> None:
    enricher = _enricher()
    enriched_results = enricher.enrich_many(
        (
            _check_result(rule_id="RG-UNKNOWN-999"),
        )
    )

    decision = ReleaseDecisionSynthesizer().synthesize(enriched_results)

    assert decision.status == ReleaseDecisionStatus.REVIEW_RECOMMENDED
    assert decision.release_allowed is True
    assert decision.missing_rule_evidence_count == 1
    assert decision.source_urls == ()
    assert decision.agent_summary == (
        "Release is allowed by the current blocking policy, but review is "
        "recommended for 0 warning check(s) and 1 result(s) without rule "
        "evidence."
    )


def test_decision_can_be_converted_to_dict() -> None:
    enricher = _enricher()
    enriched_results = enricher.enrich_many(
        (
            _check_result(rule_id="RG-DEPS-001"),
        )
    )

    data = ReleaseDecisionSynthesizer().synthesize(
        enriched_results
    ).to_dict()

    assert data["status"] == "ready"
    assert data["release_allowed"] is True
    assert data["summary"]["total"] == 1
    assert data["blocking_rule_ids"] == []
    assert data["warning_rule_ids"] == []
    assert data["missing_rule_evidence_count"] == 0
    assert data["source_urls"] == ["https://12factor.net/"]
    assert data["enriched_results"][0]["check_result"]["rule_id"] == (
        "RG-DEPS-001"
    )


def test_source_urls_are_unique_and_ordered() -> None:
    enricher = _enricher()
    enriched_results = enricher.enrich_many(
        (
            _check_result(rule_id="RG-DEPS-001"),
            _check_result(rule_id="RG-CONFIG-002"),
            _check_result(rule_id="RG-DOCKER-003"),
        )
    )

    decision = ReleaseDecisionSynthesizer().synthesize(enriched_results)

    assert decision.source_urls == (
        "https://12factor.net/",
        "https://docs.docker.com/reference/dockerfile/",
    )


def test_synthesizer_does_not_mutate_enriched_results() -> None:
    enricher = _enricher()
    enriched_results = enricher.enrich_many(
        (
            _check_result(rule_id="RG-DEPS-001"),
        )
    )
    original_data = [
        enriched_result.to_dict()
        for enriched_result in enriched_results
    ]

    ReleaseDecisionSynthesizer().synthesize(enriched_results)

    assert [
        enriched_result.to_dict()
        for enriched_result in enriched_results
    ] == original_data
