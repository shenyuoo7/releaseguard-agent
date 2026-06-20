import pytest

from releaseguard_agent.scanners.dockerfile_scanner import (
    DockerfileScanner,
)


def write_dockerfile(
    tmp_path,
    content,
    *,
    encoding="utf-8",
):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(content, encoding=encoding)
    return dockerfile


def test_scanner_returns_missing_result_without_dockerfile(
    tmp_path,
):
    result = DockerfileScanner().scan(tmp_path)

    assert result.file_path == "Dockerfile"
    assert result.exists is False
    assert result.readable is False
    assert result.has_issues is False
    assert result.instructions == ()
    assert result.parser_directives == ()
    assert result.read_error is None


def test_scanner_parses_common_docker_instructions(tmp_path):
    write_dockerfile(
        tmp_path,
        "FROM python:3.11-slim\n"
        "WORKDIR /app\n"
        "COPY . /app\n"
        'CMD ["python", "-m", "example"]\n',
    )

    result = DockerfileScanner().scan(tmp_path)

    assert result.exists is True
    assert result.readable is True
    assert result.has_issues is False
    assert tuple(
        instruction.keyword
        for instruction in result.instructions
    ) == (
        "FROM",
        "WORKDIR",
        "COPY",
        "CMD",
    )

    from_instruction = result.instructions[0]

    assert from_instruction.arguments == "python:3.11-slim"
    assert from_instruction.start_line == 1
    assert from_instruction.end_line == 1


def test_scanner_preserves_original_instruction_casing(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "from python:3.11-slim\n"
        "Run python -m pip install pytest\n",
    )

    result = DockerfileScanner().scan(tmp_path)

    assert result.instructions[0].keyword == "FROM"
    assert result.instructions[0].original_keyword == "from"
    assert result.instructions[1].keyword == "RUN"
    assert result.instructions[1].original_keyword == "Run"


def test_scanner_ignores_comments_and_blank_lines(tmp_path):
    write_dockerfile(
        tmp_path,
        "# Build the application image.\n"
        "\n"
        "FROM python:3.11-slim\n"
        "\n"
        "# Use a stable working directory.\n"
        "WORKDIR /app\n",
    )

    result = DockerfileScanner().scan(tmp_path)

    assert tuple(
        instruction.keyword
        for instruction in result.instructions
    ) == (
        "FROM",
        "WORKDIR",
    )
    assert result.instructions[0].start_line == 3
    assert result.instructions[1].start_line == 6
    assert result.issues == ()


def test_scanner_supports_utf8_bom_dockerfile(tmp_path):
    write_dockerfile(
        tmp_path,
        "FROM python:3.11-slim\n"
        "WORKDIR /应用\n",
        encoding="utf-8-sig",
    )

    result = DockerfileScanner().scan(tmp_path)

    assert result.readable is True
    assert result.instructions[0].keyword == "FROM"
    assert result.instructions[1].arguments == "/应用"


def test_scanner_joins_backslash_continuation_lines(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "FROM python:3.11-slim\n"
        "RUN python -m pip install --no-cache-dir \\\n"
        "    -r requirements.txt\n",
    )

    result = DockerfileScanner().scan(tmp_path)

    run_instruction = result.find_instructions("RUN")[0]

    assert run_instruction.arguments == (
        "python -m pip install --no-cache-dir "
        "-r requirements.txt"
    )
    assert run_instruction.start_line == 2
    assert run_instruction.end_line == 3
    assert run_instruction.raw_text == (
        "RUN python -m pip install --no-cache-dir \\\n"
        "    -r requirements.txt"
    )


def test_scanner_supports_escape_parser_directive(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "# syntax=docker/dockerfile:1\n"
        "# escape=`\n"
        "\n"
        "FROM mcr.microsoft.com/windows/servercore:ltsc2022\n"
        "RUN Write-Host first `\n"
        "    ; Write-Host second\n",
    )

    result = DockerfileScanner().scan(tmp_path)

    assert result.escape_character == "`"
    assert tuple(
        directive.key
        for directive in result.parser_directives
    ) == (
        "syntax",
        "escape",
    )
    assert result.parser_directives[1].value == "`"

    run_instruction = result.find_instructions("run")[0]

    assert run_instruction.arguments == (
        "Write-Host first ; Write-Host second"
    )
    assert run_instruction.start_line == 5
    assert run_instruction.end_line == 6


def test_scanner_preserves_global_arg_before_from(tmp_path):
    write_dockerfile(
        tmp_path,
        "ARG PYTHON_VERSION=3.11\n"
        "FROM python:${PYTHON_VERSION}-slim\n",
    )

    result = DockerfileScanner().scan(tmp_path)

    assert tuple(
        instruction.keyword
        for instruction in result.instructions
    ) == (
        "ARG",
        "FROM",
    )
    assert result.instructions[0].arguments == (
        "PYTHON_VERSION=3.11"
    )


def test_scanner_records_malformed_line_and_continues(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "not-an-instruction?\n"
        "FROM python:3.11-slim\n",
    )

    result = DockerfileScanner().scan(tmp_path)

    assert len(result.issues) == 1
    assert result.issues[0].line_number == 1
    assert "not a recognizable" in (
        result.issues[0].message
    )
    assert tuple(
        instruction.keyword
        for instruction in result.instructions
    ) == ("FROM",)


def test_scanner_records_unterminated_continuation(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "RUN echo hello " + "\\",
    )

    result = DockerfileScanner().scan(tmp_path)

    assert len(result.instructions) == 1
    assert result.instructions[0].keyword == "RUN"
    assert result.instructions[0].arguments == "echo hello"
    assert len(result.issues) == 1
    assert "unterminated" in result.issues[0].message


def test_scanner_records_invalid_escape_directive(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "# escape=/\n"
        "FROM python:3.11-slim\n",
    )

    result = DockerfileScanner().scan(tmp_path)

    assert result.escape_character == "\\"
    assert len(result.parser_directives) == 1
    assert len(result.issues) == 1
    assert "Unsupported Docker escape" in (
        result.issues[0].message
    )


def test_scan_result_can_query_and_serialize_instructions(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "FROM python:3.11-slim\n"
        "RUN echo first\n"
        "RUN echo second\n",
    )

    result = DockerfileScanner().scan(tmp_path)
    run_instructions = result.find_instructions("run")
    serialized = result.to_dict()

    assert len(run_instructions) == 2
    assert run_instructions[0].arguments == "echo first"
    assert run_instructions[1].arguments == "echo second"

    assert serialized["exists"] is True
    assert serialized["readable"] is True
    assert serialized["issues"] == []
    assert serialized["instructions"][0] == {
        "keyword": "FROM",
        "original_keyword": "FROM",
        "arguments": "python:3.11-slim",
        "start_line": 1,
        "end_line": 1,
        "raw_text": "FROM python:3.11-slim",
    }


def test_find_instructions_rejects_empty_keyword(tmp_path):
    write_dockerfile(
        tmp_path,
        "FROM python:3.11-slim\n",
    )

    result = DockerfileScanner().scan(tmp_path)

    with pytest.raises(
        ValueError,
        match="keyword must not be empty",
    ):
        result.find_instructions("")
