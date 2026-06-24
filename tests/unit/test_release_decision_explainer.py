from releaseguard_agent.agents.release_decision_explainer import (
    ReleaseDecisionExplainer,
)
from releaseguard_agent.agents.release_decision_synthesizer import (
    ReleaseDecision,
    ReleaseDecisionStatus,
    ReleaseDecisionSynthesizer,
)
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.models.rule_evidence import (
    RuleEvidence,
    RuleSourceEvidence,
)
from releaseguard_agent.rag.check_result_enricher import EnrichedCheckResult


def _check_result(
    *,
    rule_id: str | None = "RG-DEPS-001",
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


def _source_document(
    *,
    rule_id: str = "RG-DEPS-001",
    source_title: str = "Example Source",
    source_url: str = "https://example.com/source",
) -> RuleSourceEvidence:
    return RuleSourceEvidence(
        rule_id=rule_id,
        source_title=source_title,
        source_url=source_url,
        source_type="official documentation",
        rule_text="Example source rule.",
        rationale="Example rationale.",
        knowledge_file="knowledge_base/example.md",
        line_number=12,
    )


def _rule_evidence(
    *,
    rule_id: str = "RG-DEPS-001",
    rule_name: str = "Example rule",
    source_documents: tuple[RuleSourceEvidence, ...] | None = None,
) -> RuleEvidence:
    return RuleEvidence(
        rule_id=rule_id,
        rule_name=rule_name,
        checker="ExampleChecker",
        source="Example source",
        support_level="source-backed",
        priority="high",
        blocking_policy="block",
        evidence_type="example",
        phase="phase-1",
        knowledge_file="knowledge_base/rule_index.md",
        line_number=7,
        source_documents=source_documents
        if source_documents is not None
        else (_source_document(rule_id=rule_id),),
    )


def _enriched_result(
    *,
    check_result: CheckResult,
    rule_evidence: RuleEvidence | None = None,
    missing_rule_reason: str | None = None,
) -> EnrichedCheckResult:
    return EnrichedCheckResult(
        check_result=check_result,
        rule_evidence=rule_evidence,
        missing_rule_reason=missing_rule_reason,
    )


def _decision(
    *enriched_results: EnrichedCheckResult,
) -> ReleaseDecision:
    return ReleaseDecisionSynthesizer().synthesize(enriched_results)


def test_explains_ready_decision() -> None:
    decision = _decision(
        _enriched_result(
            check_result=_check_result(),
            rule_evidence=_rule_evidence(),
        )
    )

    explanation = ReleaseDecisionExplainer().explain(decision)

    assert explanation.status == ReleaseDecisionStatus.READY
    assert explanation.release_allowed is True
    assert "Release ready" in explanation.headline
    assert explanation.blocking_findings == ()
    assert explanation.warning_findings == ()
    assert explanation.missing_evidence_findings == ()
    assert explanation.to_markdown().startswith("# Release Decision")


def test_explains_blocking_findings() -> None:
    blocking_result = _check_result(
        rule_id="RG-DOCKER-003",
        status=CheckStatus.FAILED,
        risk_level=RiskLevel.HIGH,
        title="Dockerfile contract failed",
    )
    decision = _decision(
        _enriched_result(
            check_result=blocking_result,
            rule_evidence=_rule_evidence(
                rule_id="RG-DOCKER-003",
                rule_name="Dockerfile contract",
            ),
        )
    )

    explanation = ReleaseDecisionExplainer().explain(decision)

    assert explanation.status == ReleaseDecisionStatus.BLOCKED
    assert explanation.release_allowed is False
    assert len(explanation.blocking_findings) == 1
    assert explanation.blocking_findings[0].rule_id == "RG-DOCKER-003"
    assert explanation.blocking_findings[0].rule_name == (
        "Dockerfile contract"
    )
    assert "Blocking findings" in explanation.to_markdown()
    assert "Dockerfile contract failed" in explanation.to_markdown()


def test_explains_warning_findings() -> None:
    warning_result = _check_result(
        rule_id="RG-TEST-002",
        status=CheckStatus.WARNING,
        risk_level=RiskLevel.MEDIUM,
        title="Pytest configuration missing",
    )
    decision = _decision(
        _enriched_result(
            check_result=warning_result,
            rule_evidence=_rule_evidence(
                rule_id="RG-TEST-002",
                rule_name="Pytest configuration",
            ),
        )
    )

    explanation = ReleaseDecisionExplainer().explain(decision)

    assert explanation.status == ReleaseDecisionStatus.REVIEW_RECOMMENDED
    assert explanation.release_allowed is True
    assert explanation.blocking_findings == ()
    assert len(explanation.warning_findings) == 1
    assert explanation.warning_findings[0].rule_id == "RG-TEST-002"


def test_explains_missing_rule_evidence() -> None:
    unknown_result = _check_result(
        rule_id="RG-UNKNOWN-999",
        title="Unknown mapped check",
    )
    decision = _decision(
        _enriched_result(
            check_result=unknown_result,
            rule_evidence=None,
            missing_rule_reason=(
                "Rule ID 'RG-UNKNOWN-999' was not found in the rule index."
            ),
        )
    )

    explanation = ReleaseDecisionExplainer().explain(decision)

    assert explanation.status == ReleaseDecisionStatus.REVIEW_RECOMMENDED
    assert len(explanation.missing_evidence_findings) == 1
    assert explanation.missing_evidence_findings[0].rule_id == (
        "RG-UNKNOWN-999"
    )
    assert explanation.missing_evidence_findings[0].missing_rule_reason == (
        "Rule ID 'RG-UNKNOWN-999' was not found in the rule index."
    )
    assert "Missing rule evidence" in explanation.to_markdown()


def test_explanation_preserves_deduplicated_source_urls() -> None:
    first_source = _source_document(
        source_title="First Source",
        source_url="https://example.com/shared",
    )
    second_source = _source_document(
        source_title="Second Source",
        source_url="https://example.com/shared",
    )
    third_source = _source_document(
        source_title="Third Source",
        source_url="https://example.com/third",
    )
    decision = _decision(
        _enriched_result(
            check_result=_check_result(rule_id="RG-DEPS-001"),
            rule_evidence=_rule_evidence(
                rule_id="RG-DEPS-001",
                source_documents=(first_source, second_source),
            ),
        ),
        _enriched_result(
            check_result=_check_result(rule_id="RG-TEST-002"),
            rule_evidence=_rule_evidence(
                rule_id="RG-TEST-002",
                source_documents=(third_source,),
            ),
        ),
    )

    explanation = ReleaseDecisionExplainer().explain(decision)

    assert explanation.source_urls == (
        "https://example.com/shared",
        "https://example.com/third",
    )


def test_explanation_can_be_converted_to_dict() -> None:
    decision = _decision(
        _enriched_result(
            check_result=_check_result(
                status=CheckStatus.FAILED,
                risk_level=RiskLevel.HIGH,
            ),
            rule_evidence=_rule_evidence(),
        )
    )

    data = ReleaseDecisionExplainer().explain(decision).to_dict()

    assert data["status"] == "blocked"
    assert data["release_allowed"] is False
    assert data["blocking_findings"][0]["rule_id"] == "RG-DEPS-001"
    assert data["blocking_findings"][0]["source_titles"] == [
        "Example Source"
    ]
    assert data["markdown"].startswith("# Release Decision")


def test_explainer_does_not_mutate_decision() -> None:
    decision = _decision(
        _enriched_result(
            check_result=_check_result(),
            rule_evidence=_rule_evidence(),
        )
    )
    original_data = decision.to_dict()

    ReleaseDecisionExplainer().explain(decision)

    assert decision.to_dict() == original_data
