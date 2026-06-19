from releaseguard_agent.detectors.fastapi_detector import (
    FastAPIDetector,
)
from releaseguard_agent.models.check_result import (
    CheckStatus,
    RiskLevel,
)


def get_result(results, rule_id):
    for result in results:
        if result.rule_id == rule_id:
            return result

    raise AssertionError(f"Missing result for rule: {rule_id}")


def create_source_file(project_path, content):
    source_dir = project_path / "src"
    source_dir.mkdir(exist_ok=True)

    source_file = source_dir / "main.py"
    source_file.write_text(content, encoding="utf-8")

    return source_file


def create_requirements(project_path, content="fastapi==0.115.0\n"):
    requirements = project_path / "requirements.txt"
    requirements.write_text(content, encoding="utf-8")
    return requirements


def test_fastapi_detector_skips_non_fastapi_project(tmp_path):
    create_source_file(
        tmp_path,
        "VALUE = 'FastAPI() inside a string is not usage'\n",
    )
    create_requirements(tmp_path, "pytest\n")

    results = FastAPIDetector().run(tmp_path)

    dependency_result = get_result(results, "RG-FASTAPI-001")
    app_result = get_result(results, "RG-FASTAPI-002")

    assert dependency_result.status == CheckStatus.SKIPPED
    assert app_result.status == CheckStatus.SKIPPED
    assert dependency_result.should_block_release is False
    assert app_result.should_block_release is False


def test_fastapi_detector_passes_direct_import_and_app(tmp_path):
    create_requirements(tmp_path)
    create_source_file(
        tmp_path,
        "from fastapi import FastAPI\n"
        "\n"
        "app = FastAPI()\n",
    )

    results = FastAPIDetector().run(tmp_path)

    dependency_result = get_result(results, "RG-FASTAPI-001")
    app_result = get_result(results, "RG-FASTAPI-002")

    assert dependency_result.status == CheckStatus.PASSED
    assert dependency_result.risk_level == RiskLevel.INFO
    assert dependency_result.metadata["fastapi_usage_detected"] is True
    assert dependency_result.metadata["dependency_matches"][0][
        "file_path"
    ] == "requirements.txt"

    assert app_result.status == CheckStatus.PASSED
    assert app_result.metadata["app_instance_detected"] is True
    assert app_result.metadata["app_matches"][0]["target_name"] == "app"
    assert app_result.metadata["app_matches"][0]["line_number"] == 3


def test_fastapi_detector_supports_aliased_class_import(tmp_path):
    create_requirements(tmp_path)
    create_source_file(
        tmp_path,
        "from fastapi import FastAPI as API\n"
        "\n"
        "application = API()\n",
    )

    results = FastAPIDetector().run(tmp_path)
    app_result = get_result(results, "RG-FASTAPI-002")

    assert app_result.status == CheckStatus.PASSED
    assert app_result.metadata["app_matches"][0][
        "target_name"
    ] == "application"


def test_fastapi_detector_supports_module_alias_import(tmp_path):
    create_requirements(tmp_path)
    create_source_file(
        tmp_path,
        "import fastapi as framework\n"
        "\n"
        "app = framework.FastAPI()\n",
    )

    results = FastAPIDetector().run(tmp_path)
    app_result = get_result(results, "RG-FASTAPI-002")

    assert app_result.status == CheckStatus.PASSED
    assert app_result.metadata["app_instance_detected"] is True


def test_fastapi_detector_blocks_missing_dependency(tmp_path):
    create_source_file(
        tmp_path,
        "from fastapi import FastAPI\n"
        "\n"
        "app = FastAPI()\n",
    )

    results = FastAPIDetector().run(tmp_path)
    dependency_result = get_result(results, "RG-FASTAPI-001")

    assert dependency_result.status == CheckStatus.FAILED
    assert dependency_result.risk_level == RiskLevel.HIGH
    assert dependency_result.should_block_release is True
    assert dependency_result.metadata["dependency_matches"] == []
    assert dependency_result.recommendation is not None


def test_fastapi_detector_blocks_missing_app_instance(tmp_path):
    create_requirements(tmp_path)
    create_source_file(
        tmp_path,
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter()\n",
    )

    results = FastAPIDetector().run(tmp_path)

    dependency_result = get_result(results, "RG-FASTAPI-001")
    app_result = get_result(results, "RG-FASTAPI-002")

    assert dependency_result.status == CheckStatus.PASSED
    assert app_result.status == CheckStatus.FAILED
    assert app_result.risk_level == RiskLevel.HIGH
    assert app_result.should_block_release is True
    assert app_result.metadata["app_instance_detected"] is False


def test_fastapi_detector_recognizes_pyproject_dependency(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        'name = "example-api"\n'
        'dependencies = ["fastapi>=0.115", "uvicorn>=0.30"]\n',
        encoding="utf-8",
    )
    create_source_file(
        tmp_path,
        "from fastapi import FastAPI\n"
        "\n"
        "app = FastAPI()\n",
    )

    results = FastAPIDetector().run(tmp_path)
    dependency_result = get_result(results, "RG-FASTAPI-001")

    assert dependency_result.status == CheckStatus.PASSED
    assert dependency_result.metadata["dependency_matches"][0][
        "file_path"
    ] == "pyproject.toml"


def test_fastapi_detector_ignores_tests_and_virtualenv(tmp_path):
    create_requirements(tmp_path)

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_api.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n",
        encoding="utf-8",
    )

    venv_dir = tmp_path / ".venv" / "Lib" / "site-packages"
    venv_dir.mkdir(parents=True)
    (venv_dir / "installed_api.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n",
        encoding="utf-8",
    )

    results = FastAPIDetector().run(tmp_path)

    dependency_result = get_result(results, "RG-FASTAPI-001")
    app_result = get_result(results, "RG-FASTAPI-002")

    assert dependency_result.status == CheckStatus.SKIPPED
    assert app_result.status == CheckStatus.SKIPPED
    assert dependency_result.metadata["usage_matches"] == []