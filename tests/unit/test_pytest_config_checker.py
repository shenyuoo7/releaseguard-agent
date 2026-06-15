from releaseguard_agent.checkers.python.pytest_config_checker import (
    PytestConfigChecker,
)
from releaseguard_agent.models.check_result import CheckStatus, RiskLevel


def get_result_by_rule_id(results, rule_id):
    for result in results:
        if result.rule_id == rule_id:
            return result

    raise AssertionError(f"Result with rule_id {rule_id} was not found.")


def create_src_package(project_path):
    package_dir = project_path / "src" / "example_package"
    package_dir.mkdir(parents=True)
    init_file = package_dir / "__init__.py"
    init_file.write_text("", encoding="utf-8")


def test_pytest_config_checker_passes_when_pytest_ini_exists(tmp_path):
    pytest_ini = tmp_path / "pytest.ini"
    pytest_ini.write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")

    checker = PytestConfigChecker()

    results = checker.run(tmp_path)

    config_result = get_result_by_rule_id(results, "RG-TEST-006")
    src_layout_result = get_result_by_rule_id(results, "RG-TEST-007")

    assert config_result.status == CheckStatus.PASSED
    assert config_result.risk_level == RiskLevel.INFO
    assert config_result.metadata["found_configs"] == ["pytest.ini"]
    assert config_result.metadata["support_level"] == "releaseguard-default"

    assert src_layout_result.status == CheckStatus.SKIPPED
    assert src_layout_result.metadata["src_layout_detected"] is False


def test_pytest_config_checker_warns_when_config_is_missing(tmp_path):
    checker = PytestConfigChecker()

    results = checker.run(tmp_path)

    config_result = get_result_by_rule_id(results, "RG-TEST-006")
    src_layout_result = get_result_by_rule_id(results, "RG-TEST-007")

    assert config_result.status == CheckStatus.WARNING
    assert config_result.risk_level == RiskLevel.MEDIUM
    assert config_result.should_block_release is False
    assert config_result.metadata["found_configs"] == []

    assert src_layout_result.status == CheckStatus.SKIPPED


def test_pytest_config_checker_does_not_count_bare_pyproject_as_pytest_config(
    tmp_path,
):
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text(
        '[project]\nname = "example-project"\n',
        encoding="utf-8",
    )

    checker = PytestConfigChecker()

    results = checker.run(tmp_path)

    config_result = get_result_by_rule_id(results, "RG-TEST-006")

    assert config_result.status == CheckStatus.WARNING
    assert config_result.metadata["found_configs"] == []


def test_src_layout_passes_when_pytest_ini_sets_pythonpath_src(tmp_path):
    create_src_package(tmp_path)
    pytest_ini = tmp_path / "pytest.ini"
    pytest_ini.write_text(
        "[pytest]\npythonpath = src\ntestpaths = tests\n",
        encoding="utf-8",
    )

    checker = PytestConfigChecker()

    results = checker.run(tmp_path)

    config_result = get_result_by_rule_id(results, "RG-TEST-006")
    src_layout_result = get_result_by_rule_id(results, "RG-TEST-007")

    assert config_result.status == CheckStatus.PASSED

    assert src_layout_result.status == CheckStatus.PASSED
    assert src_layout_result.risk_level == RiskLevel.INFO
    assert src_layout_result.metadata["src_layout_detected"] is True
    assert src_layout_result.metadata["pythonpath_entries"] == ["src"]
    assert src_layout_result.metadata["accepted_config_files"] == ["pytest.ini"]


def test_src_layout_passes_when_pyproject_sets_pythonpath_src(tmp_path):
    create_src_package(tmp_path)
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text(
        "[tool.pytest.ini_options]\n"
        'pythonpath = ["src"]\n'
        'testpaths = ["tests"]\n',
        encoding="utf-8",
    )

    checker = PytestConfigChecker()

    results = checker.run(tmp_path)

    config_result = get_result_by_rule_id(results, "RG-TEST-006")
    src_layout_result = get_result_by_rule_id(results, "RG-TEST-007")

    assert config_result.status == CheckStatus.PASSED
    assert config_result.metadata["found_configs"] == ["pyproject.toml"]

    assert src_layout_result.status == CheckStatus.PASSED
    assert src_layout_result.metadata["pythonpath_entries"] == ["src"]
    assert src_layout_result.metadata["accepted_config_files"] == [
        "pyproject.toml"
    ]


def test_src_layout_warns_when_pythonpath_src_is_missing(tmp_path):
    create_src_package(tmp_path)
    pytest_ini = tmp_path / "pytest.ini"
    pytest_ini.write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")

    checker = PytestConfigChecker()

    results = checker.run(tmp_path)

    config_result = get_result_by_rule_id(results, "RG-TEST-006")
    src_layout_result = get_result_by_rule_id(results, "RG-TEST-007")

    assert config_result.status == CheckStatus.PASSED

    assert src_layout_result.status == CheckStatus.WARNING
    assert src_layout_result.risk_level == RiskLevel.MEDIUM
    assert src_layout_result.should_block_release is False
    assert src_layout_result.metadata["src_layout_detected"] is True
    assert src_layout_result.metadata["pythonpath_entries"] == []
    assert src_layout_result.metadata["accepted_config_files"] == []


def test_pytest_config_checker_returns_two_rule_results(tmp_path):
    checker = PytestConfigChecker()

    results = checker.run(tmp_path)

    rule_ids = {result.rule_id for result in results}

    assert rule_ids == {"RG-TEST-006", "RG-TEST-007"}