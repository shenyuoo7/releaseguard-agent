from releaseguard_agent.checkers.common.env_example_checker import (
    EnvExampleChecker,
)
from releaseguard_agent.models.check_result import CheckStatus, RiskLevel


def test_env_example_checker_passes_when_env_example_exists(tmp_path):
    env_example_file = tmp_path / ".env.example"
    env_example_file.write_text(
        "DATABASE_URL=postgresql://example\nSECRET_KEY=change-me\n",
        encoding="utf-8",
    )

    checker = EnvExampleChecker()

    results = checker.run(tmp_path)

    assert len(results) == 1
    result = results[0]

    assert result.status == CheckStatus.PASSED
    assert result.risk_level == RiskLevel.INFO
    assert result.passed is True
    assert result.failed is False
    assert result.should_block_release is False
    assert result.rule_id == "RG-CONFIG-001"
    assert result.metadata["found_file"] == ".env.example"
    assert result.metadata["support_level"] == "releaseguard-default"


def test_env_example_checker_fails_when_env_usage_exists_but_file_missing(tmp_path):
    app_file = tmp_path / "app.py"
    app_file.write_text(
        "import os\nDATABASE_URL = os.getenv('DATABASE_URL')\n",
        encoding="utf-8",
    )

    checker = EnvExampleChecker()

    results = checker.run(tmp_path)

    assert len(results) == 1
    result = results[0]

    assert result.status == CheckStatus.FAILED
    assert result.risk_level == RiskLevel.HIGH
    assert result.failed is True
    assert result.should_block_release is True
    assert result.rule_id == "RG-CONFIG-001"
    assert result.metadata["env_usage_detected"] is True
    assert result.metadata["found_file"] is None
    assert any("app.py:2" in item for item in result.evidence)


def test_env_example_checker_warns_when_file_missing_and_no_env_usage_found(tmp_path):
    app_file = tmp_path / "app.py"
    app_file.write_text("print('hello')\n", encoding="utf-8")

    checker = EnvExampleChecker()

    result = checker.run(tmp_path)[0]

    assert result.status == CheckStatus.WARNING
    assert result.risk_level == RiskLevel.MEDIUM
    assert result.passed is False
    assert result.failed is False
    assert result.should_block_release is False
    assert result.metadata["env_usage_detected"] is False
    assert result.recommendation is not None


def test_env_example_checker_only_accepts_root_env_example(tmp_path):
    nested_dir = tmp_path / "config"
    nested_dir.mkdir()
    nested_env_example = nested_dir / ".env.example"
    nested_env_example.write_text("DATABASE_URL=example\n", encoding="utf-8")

    checker = EnvExampleChecker()

    result = checker.run(tmp_path)[0]

    assert result.status == CheckStatus.WARNING
    assert result.metadata["found_file"] is None


def test_env_example_checker_ignores_virtual_environment_files(tmp_path):
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    ignored_file = venv_dir / "ignored.py"
    ignored_file.write_text(
        "import os\nTOKEN = os.getenv('TOKEN')\n",
        encoding="utf-8",
    )

    checker = EnvExampleChecker()

    result = checker.run(tmp_path)[0]

    assert result.status == CheckStatus.WARNING
    assert result.metadata["env_usage_detected"] is False