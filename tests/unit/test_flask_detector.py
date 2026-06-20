from releaseguard_agent.detectors.flask_detector import FlaskDetector
from releaseguard_agent.models.check_result import (
    CheckStatus,
    RiskLevel,
)


def get_result(results, rule_id):
    for result in results:
        if result.rule_id == rule_id:
            return result

    raise AssertionError(f"Missing result for rule: {rule_id}")


def create_source_file(
    project_path,
    content,
    *,
    encoding="utf-8",
):
    source_dir = project_path / "src"
    source_dir.mkdir(exist_ok=True)
    source_file = source_dir / "main.py"
    source_file.write_text(content, encoding=encoding)
    return source_file


def create_requirements(
    project_path,
    content="Flask==3.1.0\n",
    *,
    encoding="utf-8",
):
    requirements = project_path / "requirements.txt"
    requirements.write_text(content, encoding=encoding)
    return requirements


def test_flask_detector_skips_non_flask_project(tmp_path):
    create_source_file(tmp_path, "VALUE = 'Flask is only text'\n")
    create_requirements(tmp_path, "pytest\n")

    results = FlaskDetector().run(tmp_path)

    assert len(results) == 4
    assert all(
        result.status == CheckStatus.SKIPPED
        for result in results
    )
    assert all(
        result.should_block_release is False
        for result in results
    )


def test_flask_detector_passes_direct_import_and_app(tmp_path):
    create_requirements(tmp_path)
    create_source_file(
        tmp_path,
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n",
    )

    results = FlaskDetector().run(tmp_path)

    dependency = get_result(results, "RG-FLASK-001")
    app = get_result(results, "RG-FLASK-002")
    server = get_result(results, "RG-FLASK-003")
    debug = get_result(results, "RG-SEC-002")

    assert dependency.status == CheckStatus.PASSED
    assert app.status == CheckStatus.PASSED
    assert server.status == CheckStatus.PASSED
    assert debug.status == CheckStatus.PASSED
    assert app.metadata["app_matches"][0]["target_name"] == "app"


def test_flask_detector_supports_aliased_class_import(tmp_path):
    create_requirements(tmp_path)
    create_source_file(
        tmp_path,
        "from flask import Flask as WebApplication\n"
        "\n"
        "application = WebApplication(__name__)\n",
    )

    results = FlaskDetector().run(tmp_path)
    app = get_result(results, "RG-FLASK-002")

    assert app.status == CheckStatus.PASSED
    assert (
        app.metadata["app_matches"][0]["target_name"]
        == "application"
    )


def test_flask_detector_supports_module_alias_import(tmp_path):
    create_requirements(tmp_path)
    create_source_file(
        tmp_path,
        "import flask as framework\n"
        "\n"
        "app = framework.Flask(__name__)\n",
    )

    results = FlaskDetector().run(tmp_path)
    app = get_result(results, "RG-FLASK-002")

    assert app.status == CheckStatus.PASSED


def test_flask_detector_supports_utf8_bom_files(tmp_path):
    create_requirements(tmp_path, encoding="utf-8-sig")
    create_source_file(
        tmp_path,
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n",
        encoding="utf-8-sig",
    )

    results = FlaskDetector().run(tmp_path)

    dependency = get_result(results, "RG-FLASK-001")
    app = get_result(results, "RG-FLASK-002")

    assert dependency.status == CheckStatus.PASSED
    assert app.status == CheckStatus.PASSED
    assert app.metadata["parse_errors"] == []


def test_flask_detector_blocks_missing_dependency(tmp_path):
    create_source_file(
        tmp_path,
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n",
    )

    results = FlaskDetector().run(tmp_path)
    dependency = get_result(results, "RG-FLASK-001")

    assert dependency.status == CheckStatus.FAILED
    assert dependency.risk_level == RiskLevel.HIGH
    assert dependency.should_block_release is True
    assert dependency.metadata["dependency_matches"] == []


def test_flask_detector_blocks_missing_app_instance(tmp_path):
    create_requirements(tmp_path)
    create_source_file(
        tmp_path,
        "from flask import Blueprint\n"
        "\n"
        "routes = Blueprint('routes', __name__)\n",
    )

    results = FlaskDetector().run(tmp_path)
    app = get_result(results, "RG-FLASK-002")

    assert app.status == CheckStatus.FAILED
    assert app.risk_level == RiskLevel.HIGH
    assert app.should_block_release is True


def test_flask_detector_warns_about_development_server(tmp_path):
    create_requirements(tmp_path)
    create_source_file(
        tmp_path,
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n"
        "app.run()\n",
    )

    results = FlaskDetector().run(tmp_path)
    server = get_result(results, "RG-FLASK-003")
    debug = get_result(results, "RG-SEC-002")

    assert server.status == CheckStatus.WARNING
    assert server.risk_level == RiskLevel.MEDIUM
    assert server.should_block_release is False
    assert debug.status == CheckStatus.PASSED


def test_flask_detector_blocks_explicit_debug_true(tmp_path):
    create_requirements(tmp_path)
    create_source_file(
        tmp_path,
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n"
        "app.run(debug=True)\n",
    )

    results = FlaskDetector().run(tmp_path)
    debug = get_result(results, "RG-SEC-002")

    assert debug.status == CheckStatus.FAILED
    assert debug.risk_level == RiskLevel.HIGH
    assert debug.should_block_release is True
    assert debug.metadata["debug_true_detected"] is True


def test_flask_detector_accepts_explicit_debug_false(tmp_path):
    create_requirements(tmp_path)
    create_source_file(
        tmp_path,
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n"
        "app.run(debug=False)\n",
    )

    results = FlaskDetector().run(tmp_path)

    server = get_result(results, "RG-FLASK-003")
    debug = get_result(results, "RG-SEC-002")

    assert server.status == CheckStatus.WARNING
    assert debug.status == CheckStatus.PASSED
    assert debug.metadata["debug_true_detected"] is False


def test_flask_detector_warns_about_dynamic_debug_value(tmp_path):
    create_requirements(tmp_path)
    create_source_file(
        tmp_path,
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n"
        "DEBUG_ENABLED = False\n"
        "app.run(debug=DEBUG_ENABLED)\n",
    )

    results = FlaskDetector().run(tmp_path)
    debug = get_result(results, "RG-SEC-002")

    assert debug.status == CheckStatus.WARNING
    assert debug.risk_level == RiskLevel.MEDIUM
    assert debug.should_block_release is False
    assert debug.metadata["dynamic_debug_detected"] is True


def test_flask_detector_ignores_tests_and_virtualenv(tmp_path):
    create_requirements(tmp_path)

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n",
        encoding="utf-8",
    )

    installed_dir = (
        tmp_path / ".venv" / "Lib" / "site-packages"
    )
    installed_dir.mkdir(parents=True)
    (installed_dir / "installed_app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n",
        encoding="utf-8",
    )

    results = FlaskDetector().run(tmp_path)

    assert all(
        result.status == CheckStatus.SKIPPED
        for result in results
    )
    assert all(
        result.metadata["usage_matches"] == []
        for result in results
    )

def test_flask_detector_detects_cross_file_imported_app_startup(
    tmp_path,
):
    create_requirements(tmp_path)

    source_dir = tmp_path / "src"
    source_dir.mkdir()

    (source_dir / "app.py").write_text(
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n",
        encoding="utf-8",
    )
    (source_dir / "main.py").write_text(
        "from app import app as application\n"
        "\n"
        "application.run(debug=True)\n",
        encoding="utf-8",
    )

    results = FlaskDetector().run(tmp_path)

    server = get_result(results, "RG-FLASK-003")
    debug = get_result(results, "RG-SEC-002")

    assert server.status == CheckStatus.WARNING
    assert debug.status == CheckStatus.FAILED
    assert debug.should_block_release is True
    assert server.metadata["run_matches"][0]["target_name"] == (
        "application"
    )


def test_flask_detector_ignores_unrelated_imported_run_call(
    tmp_path,
):
    create_requirements(tmp_path)

    source_dir = tmp_path / "src"
    source_dir.mkdir()

    (source_dir / "app.py").write_text(
        "from flask import Flask\n"
        "\n"
        "app = Flask(__name__)\n",
        encoding="utf-8",
    )
    (source_dir / "main.py").write_text(
        "from worker import worker\n"
        "\n"
        "worker.run(debug=True)\n",
        encoding="utf-8",
    )

    results = FlaskDetector().run(tmp_path)

    server = get_result(results, "RG-FLASK-003")
    debug = get_result(results, "RG-SEC-002")

    assert server.status == CheckStatus.PASSED
    assert debug.status == CheckStatus.PASSED
