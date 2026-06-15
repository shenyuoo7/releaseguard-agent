from releaseguard_agent.checkers.python.dependency_checker import DependencyChecker
from releaseguard_agent.models.check_result import CheckStatus, RiskLevel


def test_dependency_checker_passes_when_requirements_txt_exists(tmp_path):
    requirements_file = tmp_path / "requirements.txt"
    requirements_file.write_text("fastapi\npytest\n", encoding="utf-8")

    checker = DependencyChecker()

    results = checker.run(tmp_path)

    assert len(results) == 1
    result = results[0]

    assert result.status == CheckStatus.PASSED
    assert result.risk_level == RiskLevel.INFO
    assert result.passed is True
    assert result.failed is False
    assert result.should_block_release is False
    assert result.rule_id == "RG-DEPS-001"
    assert result.rule_source == "The Twelve-Factor App - Dependencies"
    assert "Found dependency file: requirements.txt" in result.evidence
    assert result.metadata["found_files"] == ["requirements.txt"]


def test_dependency_checker_passes_when_pyproject_toml_exists(tmp_path):
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text(
        '[project]\nname = "example-project"\n',
        encoding="utf-8",
    )

    checker = DependencyChecker()

    results = checker.run(tmp_path)

    assert len(results) == 1
    result = results[0]

    assert result.status == CheckStatus.PASSED
    assert result.rule_id == "RG-DEPS-001"
    assert "Found dependency file: pyproject.toml" in result.evidence
    assert result.metadata["found_files"] == ["pyproject.toml"]


def test_dependency_checker_fails_when_no_dependency_file_exists(tmp_path):
    checker = DependencyChecker()

    results = checker.run(tmp_path)

    assert len(results) == 1
    result = results[0]

    assert result.status == CheckStatus.FAILED
    assert result.risk_level == RiskLevel.HIGH
    assert result.passed is False
    assert result.failed is True
    assert result.should_block_release is True
    assert result.rule_id == "RG-DEPS-001"
    assert result.recommendation is not None
    assert result.metadata["found_files"] == []


def test_dependency_checker_records_checked_files_when_missing(tmp_path):
    checker = DependencyChecker()

    result = checker.run(tmp_path)[0]

    assert "requirements.txt" in result.metadata["checked_files"]
    assert "pyproject.toml" in result.metadata["checked_files"]
    assert "No dependency declaration file was found in the project root." in result.evidence


def test_dependency_checker_only_checks_project_root(tmp_path):
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    nested_requirements = nested_dir / "requirements.txt"
    nested_requirements.write_text("pytest\n", encoding="utf-8")

    checker = DependencyChecker()

    result = checker.run(tmp_path)[0]

    assert result.status == CheckStatus.FAILED
    assert result.metadata["found_files"] == []