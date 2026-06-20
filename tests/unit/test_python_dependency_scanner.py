import pytest

from releaseguard_agent.scanners.python_dependency_scanner import (
    PythonDependencyScanner,
)


def test_scanner_finds_normalized_requirements_dependency(
    tmp_path,
):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        "# Runtime dependencies\n"
        "pytest>=8\n"
        "My_Package[standard]>=1.0\n",
        encoding="utf-8",
    )

    matches = PythonDependencyScanner().find_matches(
        tmp_path,
        "my-package",
    )

    assert len(matches) == 1
    assert matches[0].file_path == "requirements.txt"
    assert matches[0].line_number == 3
    assert matches[0].declaration == (
        "My_Package[standard]>=1.0"
    )
    assert matches[0].to_dict() == {
        "file_path": "requirements.txt",
        "line_number": 3,
        "declaration": "My_Package[standard]>=1.0",
    }


def test_scanner_ignores_comments_options_and_other_packages(
    tmp_path,
):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        "# fastapi is mentioned only in a comment\n"
        "--index-url https://example.com/simple\n"
        "pytest>=8\n",
        encoding="utf-8",
    )

    matches = PythonDependencyScanner().find_matches(
        tmp_path,
        "fastapi",
    )

    assert matches == []


@pytest.mark.parametrize(
    ("file_name", "content", "package_name"),
    [
        (
            "pyproject.toml",
            "[project]\n"
            'dependencies = ["fastapi>=0.115", "uvicorn>=0.30"]\n',
            "fastapi",
        ),
        (
            "pyproject.toml",
            "[tool.poetry.dependencies]\n"
            'python = "^3.11"\n'
            'Flask = "^3.1"\n',
            "flask",
        ),
        (
            "Pipfile",
            "[packages]\n"
            'fastapi = "*"\n'
            'pytest = "*"\n',
            "fastapi",
        ),
        (
            "poetry.lock",
            "[[package]]\n"
            'name = "flask"\n'
            'version = "3.1.0"\n',
            "flask",
        ),
        (
            "uv.lock",
            "[[package]]\n"
            'name = "fastapi"\n'
            'version = "0.115.0"\n',
            "fastapi",
        ),
    ],
)
def test_scanner_finds_toml_dependency_formats(
    tmp_path,
    file_name,
    content,
    package_name,
):
    dependency_file = tmp_path / file_name
    dependency_file.write_text(
        content,
        encoding="utf-8",
    )

    matches = PythonDependencyScanner().find_matches(
        tmp_path,
        package_name,
    )

    assert len(matches) == 1
    assert matches[0].file_path == file_name
    assert matches[0].line_number is not None
    assert package_name.lower() in (
        matches[0].declaration.lower()
    )


def test_scanner_finds_setup_cfg_install_requires(tmp_path):
    setup_cfg = tmp_path / "setup.cfg"
    setup_cfg.write_text(
        "[metadata]\n"
        "name = example-project\n"
        "\n"
        "[options]\n"
        "install_requires =\n"
        "    Flask>=3.0\n"
        "    pytest>=8\n",
        encoding="utf-8",
    )

    matches = PythonDependencyScanner().find_matches(
        tmp_path,
        "flask",
    )

    assert len(matches) == 1
    assert matches[0].file_path == "setup.cfg"
    assert matches[0].declaration == "Flask>=3.0"
    assert matches[0].line_number == 6


def test_scanner_finds_setup_py_install_requires(tmp_path):
    setup_py = tmp_path / "setup.py"
    setup_py.write_text(
        "from setuptools import setup\n"
        "\n"
        "setup(\n"
        '    name="example-project",\n'
        '    install_requires=["FastAPI>=0.115", "uvicorn"],\n'
        ")\n",
        encoding="utf-8",
    )

    matches = PythonDependencyScanner().find_matches(
        tmp_path,
        "fastapi",
    )

    assert len(matches) == 1
    assert matches[0].file_path == "setup.py"
    assert matches[0].declaration == "FastAPI>=0.115"
    assert matches[0].line_number == 5


def test_scanner_supports_utf8_bom_files(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        "fastapi[standard]>=0.115\n",
        encoding="utf-8-sig",
    )

    matches = PythonDependencyScanner().find_matches(
        tmp_path,
        "fastapi",
    )

    assert len(matches) == 1
    assert matches[0].line_number == 1


def test_scanner_returns_empty_for_missing_or_malformed_files(
    tmp_path,
):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project\ninvalid = true",
        encoding="utf-8",
    )

    matches = PythonDependencyScanner().find_matches(
        tmp_path,
        "fastapi",
    )

    assert matches == []


def test_scanner_rejects_invalid_package_name(tmp_path):
    scanner = PythonDependencyScanner()

    with pytest.raises(
        ValueError,
        match="valid base package name",
    ):
        scanner.find_matches(tmp_path, "")