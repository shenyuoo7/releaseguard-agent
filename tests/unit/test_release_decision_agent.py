from pathlib import Path

from releaseguard_agent.agents.release_decision_agent import (
    ReleaseDecisionAgent,
)
from releaseguard_agent.agents.release_decision_synthesizer import (
    ReleaseDecisionStatus,
)
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
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


def test_agent_synthesizes_ready_decision_from_raw_results() -> None:
    agent = ReleaseDecisionAgent.from_rule_index(RULE_INDEX_PATH)

    decision = agent.decide(
        (
            _check_result(rule_id="RG-DEPS-001"),
            _check_result(rule_id="RG-TEST-002"),
        )
    )

    assert decision.status == ReleaseDecisionStatus.READY
    assert decision.release_allowed is True
    assert decision.summary["total"] == 2
    assert decision.summary["blocking"] == 0
    assert decision.enriched_results[0].rule_evidence is not None
    assert decision.enriched_results[0].rule_evidence.rule_id == (
        "RG-DEPS-001"
    )


def test_agent_synthesizes_blocked_decision_from_raw_results() -> None:
    agent = ReleaseDecisionAgent.from_rule_index(RULE_INDEX_PATH)

    decision = agent.decide(
        (
            _check_result(rule_id="RG-DEPS-001"),
            _check_result(
                rule_id="RG-DOCKER-003",
                status=CheckStatus.FAILED,
                risk_level=RiskLevel.HIGH,
                title="Invalid Dockerfile instruction order",
            ),
        )
    )

    assert decision.status == ReleaseDecisionStatus.BLOCKED
    assert decision.release_allowed is False
    assert decision.blocking_rule_ids == ("RG-DOCKER-003",)
    assert decision.summary["blocking"] == 1


def test_agent_synthesizes_review_decision_for_unknown_rule() -> None:
    agent = ReleaseDecisionAgent.from_rule_index(RULE_INDEX_PATH)

    decision = agent.decide(
        (
            _check_result(rule_id="RG-UNKNOWN-999"),
        )
    )

    assert decision.status == ReleaseDecisionStatus.REVIEW_RECOMMENDED
    assert decision.release_allowed is True
    assert decision.missing_rule_evidence_count == 1
    assert decision.enriched_results[0].missing_rule_reason == (
        "Rule ID 'RG-UNKNOWN-999' was not found in the rule index."
    )


def test_agent_preserves_input_order_in_enriched_results() -> None:
    agent = ReleaseDecisionAgent.from_rule_index(RULE_INDEX_PATH)
    first = _check_result(rule_id="RG-DEPS-001")
    second = _check_result(rule_id="RG-DOCKER-003")
    third = _check_result(rule_id="RG-TEST-002")

    decision = agent.decide((first, second, third))

    assert decision.enriched_results[0].check_result is first
    assert decision.enriched_results[1].check_result is second
    assert decision.enriched_results[2].check_result is third


def test_agent_does_not_mutate_original_check_results() -> None:
    agent = ReleaseDecisionAgent.from_rule_index(RULE_INDEX_PATH)
    first = _check_result(rule_id="RG-DEPS-001")
    second = _check_result(
        rule_id="RG-DOCKER-003",
        status=CheckStatus.FAILED,
        risk_level=RiskLevel.HIGH,
    )
    original_data = [first.to_dict(), second.to_dict()]

    agent.decide((first, second))

    assert [first.to_dict(), second.to_dict()] == original_data


def test_agent_decision_can_be_converted_to_dict() -> None:
    agent = ReleaseDecisionAgent.from_rule_index(RULE_INDEX_PATH)

    data = agent.decide(
        (
            _check_result(rule_id="RG-DEPS-001"),
        )
    ).to_dict()

    assert data["status"] == "ready"
    assert data["release_allowed"] is True
    assert data["summary"]["total"] == 1
    assert data["enriched_results"][0]["check_result"]["rule_id"] == (
        "RG-DEPS-001"
    )
    assert data["enriched_results"][0]["rule_evidence"]["rule_id"] == (
        "RG-DEPS-001"
    )
