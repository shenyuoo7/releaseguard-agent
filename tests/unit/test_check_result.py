from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)


def test_passed_property_is_true_for_passed_status() -> None:
    result = CheckResult(
        checker_name="readme_checker",
        status=CheckStatus.PASSED,
        risk_level=RiskLevel.INFO,
        title="README exists",
        message="README.md was found.",
    )

    assert result.passed is True
    assert result.failed is False


def test_failed_property_is_true_for_failed_status() -> None:
    result = CheckResult(
        checker_name="docker_checker",
        status=CheckStatus.FAILED,
        risk_level=RiskLevel.HIGH,
        title="Dockerfile missing",
        message="No Dockerfile was found.",
    )

    assert result.failed is True
    assert result.passed is False


def test_high_failed_result_should_block_release() -> None:
    result = CheckResult(
        checker_name="test_checker",
        status=CheckStatus.FAILED,
        risk_level=RiskLevel.HIGH,
        title="Tests failed",
        message="The test command returned a non-zero exit code.",
    )

    assert result.should_block_release is True


def test_warning_high_result_should_not_block_release() -> None:
    result = CheckResult(
        checker_name="dependency_checker",
        status=CheckStatus.WARNING,
        risk_level=RiskLevel.HIGH,
        title="Unpinned dependency",
        message="Some dependencies are not pinned.",
    )

    assert result.should_block_release is False


def test_failed_medium_result_should_not_block_release() -> None:
    result = CheckResult(
        checker_name="readme_checker",
        status=CheckStatus.FAILED,
        risk_level=RiskLevel.MEDIUM,
        title="README incomplete",
        message="README.md is missing release instructions.",
    )

    assert result.should_block_release is False


def test_to_dict_converts_enums_to_strings() -> None:
    result = CheckResult(
        checker_name="env_checker",
        status=CheckStatus.FAILED,
        risk_level=RiskLevel.CRITICAL,
        title="Missing environment variables",
        message="Required environment variables are missing.",
        evidence=["Missing OPENAI_API_KEY"],
        recommendation="Add required variables to .env.example.",
        rule_id="RG-ENV-001",
        rule_source="knowledge_base/release_rules/env.md",
        file_path=".env.example",
        metadata={"missing_keys": ["OPENAI_API_KEY"]},
    )

    data = result.to_dict()

    assert data["checker_name"] == "env_checker"
    assert data["status"] == "failed"
    assert data["risk_level"] == "critical"
    assert data["evidence"] == ["Missing OPENAI_API_KEY"]
    assert data["recommendation"] == "Add required variables to .env.example."
    assert data["rule_id"] == "RG-ENV-001"
    assert data["rule_source"] == "knowledge_base/release_rules/env.md"
    assert data["file_path"] == ".env.example"
    assert data["metadata"] == {"missing_keys": ["OPENAI_API_KEY"]}
    assert data["passed"] is False
    assert data["failed"] is True
    assert data["should_block_release"] is True