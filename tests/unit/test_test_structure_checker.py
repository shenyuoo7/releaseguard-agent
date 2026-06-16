from releaseguard_agent.checkers.python.test_structure_checker import (
    TestStructureChecker,
)
from releaseguard_agent.models.check_result import CheckStatus, RiskLevel


def get_result_by_rule_id(results, rule_id):
    for result in results:
        if result.rule_id == rule_id:
            return result

    raise AssertionError(f"Result with rule_id {rule_id} was not found.")


def normalize_paths(paths):
    return {path.replace("\\", "/") for path in paths}


def test_test_structure_checker_passes_when_tests_dir_and_test_file_exist(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_example.py"
    test_file.write_text("def test_example():\n    assert True\n", encoding="utf-8")

    checker = TestStructureChecker()

    results = checker.run(tmp_path)

    tests_dir_result = get_result_by_rule_id(results, "RG-TEST-001")
    test_files_result = get_result_by_rule_id(results, "RG-TEST-002")

    assert tests_dir_result.status == CheckStatus.PASSED
    assert tests_dir_result.risk_level == RiskLevel.INFO
    assert tests_dir_result.passed is True

    assert test_files_result.status == CheckStatus.PASSED
    assert test_files_result.risk_level == RiskLevel.INFO
    assert test_files_result.metadata["found_file_count"] == 1
    assert normalize_paths(test_files_result.metadata["found_files"]) == {
        "tests/test_example.py"
    }


def test_test_structure_checker_warns_when_tests_directory_is_missing(tmp_path):
    test_file = tmp_path / "test_example.py"
    test_file.write_text("def test_example():\n    assert True\n", encoding="utf-8")

    checker = TestStructureChecker()

    results = checker.run(tmp_path)

    tests_dir_result = get_result_by_rule_id(results, "RG-TEST-001")
    test_files_result = get_result_by_rule_id(results, "RG-TEST-002")

    assert tests_dir_result.status == CheckStatus.WARNING
    assert tests_dir_result.risk_level == RiskLevel.MEDIUM
    assert tests_dir_result.should_block_release is False

    assert test_files_result.status == CheckStatus.PASSED
    assert test_files_result.metadata["found_file_count"] == 1


def test_test_structure_checker_fails_when_no_test_files_exist(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    helper_file = tests_dir / "helper.py"
    helper_file.write_text("VALUE = 1\n", encoding="utf-8")

    checker = TestStructureChecker()

    results = checker.run(tmp_path)

    tests_dir_result = get_result_by_rule_id(results, "RG-TEST-001")
    test_files_result = get_result_by_rule_id(results, "RG-TEST-002")

    assert tests_dir_result.status == CheckStatus.PASSED

    assert test_files_result.status == CheckStatus.FAILED
    assert test_files_result.risk_level == RiskLevel.HIGH
    assert test_files_result.failed is True
    assert test_files_result.should_block_release is True
    assert test_files_result.metadata["found_file_count"] == 0
    assert test_files_result.metadata["found_files"] == []


def test_test_structure_checker_recognizes_star_test_py_pattern(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "example_test.py"
    test_file.write_text("def test_example():\n    assert True\n", encoding="utf-8")

    checker = TestStructureChecker()

    results = checker.run(tmp_path)

    test_files_result = get_result_by_rule_id(results, "RG-TEST-002")

    assert test_files_result.status == CheckStatus.PASSED
    assert test_files_result.metadata["found_file_count"] == 1


def test_test_structure_checker_ignores_virtual_environment_files(tmp_path):
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    ignored_test_file = venv_dir / "test_installed_package.py"
    ignored_test_file.write_text(
        "def test_installed_package():\n    assert True\n",
        encoding="utf-8",
    )

    checker = TestStructureChecker()

    results = checker.run(tmp_path)

    test_files_result = get_result_by_rule_id(results, "RG-TEST-002")

    assert test_files_result.status == CheckStatus.FAILED
    assert test_files_result.metadata["found_file_count"] == 0


def test_test_structure_checker_respects_pytest_testpaths(tmp_path):
    pytest_config = tmp_path / "pytest.ini"
    pytest_config.write_text(
        "[pytest]\n"
        "testpaths = tests\n",
        encoding="utf-8",
    )

    source_checker_dir = (
        tmp_path / "src" / "releaseguard_agent" / "checkers" / "python"
    )
    source_checker_dir.mkdir(parents=True)
    source_checker_file = source_checker_dir / "test_structure_checker.py"
    source_checker_file.write_text("VALUE = 1\n", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    real_test_file = tests_dir / "test_example.py"
    real_test_file.write_text(
        "def test_example():\n    assert True\n",
        encoding="utf-8",
    )

    checker = TestStructureChecker()

    results = checker.run(tmp_path)

    test_files_result = get_result_by_rule_id(results, "RG-TEST-002")
    found_files = normalize_paths(test_files_result.metadata["found_files"])

    assert test_files_result.status == CheckStatus.PASSED
    assert test_files_result.metadata["configured_testpaths"] == ["tests"]
    assert normalize_paths(test_files_result.metadata["search_roots"]) == {"tests"}
    assert test_files_result.metadata["found_file_count"] == 1
    assert found_files == {"tests/test_example.py"}
    assert (
        "src/releaseguard_agent/checkers/python/test_structure_checker.py"
        not in found_files
    )


def test_test_structure_checker_returns_two_rule_results(tmp_path):
    checker = TestStructureChecker()

    results = checker.run(tmp_path)

    rule_ids = {result.rule_id for result in results}

    assert rule_ids == {"RG-TEST-001", "RG-TEST-002"}
