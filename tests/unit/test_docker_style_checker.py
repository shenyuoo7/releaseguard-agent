from releaseguard_agent.checkers.common.docker_style_checker import (
    DockerStyleChecker,
)
from releaseguard_agent.models.check_result import (
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.scanners.dockerfile_scanner import (
    DockerfileScan,
)


def write_dockerfile(tmp_path, content):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(content, encoding="utf-8")
    return dockerfile


def test_style_checker_skips_when_dockerfile_missing(
    tmp_path,
):
    result = DockerStyleChecker().run(tmp_path)[0]

    assert result.rule_id == "RG-DOCKER-008"
    assert result.status == CheckStatus.SKIPPED
    assert result.risk_level == RiskLevel.INFO
    assert result.should_block_release is False
    assert result.metadata["dockerfile_exists"] is False
    assert result.metadata["checked_instruction_count"] == 0


def test_style_checker_skips_unreadable_dockerfile(
    tmp_path,
):
    class UnreadableScanner:
        def scan(self, project_path):
            return DockerfileScan(
                file_path="Dockerfile",
                exists=True,
                escape_character="\\",
                parser_directives=(),
                instructions=(),
                issues=(),
                read_error="PermissionError: access denied",
            )

    result = DockerStyleChecker(
        scanner=UnreadableScanner(),
    ).run(tmp_path)[0]

    assert result.status == CheckStatus.SKIPPED
    assert result.risk_level == RiskLevel.INFO
    assert result.should_block_release is False
    assert result.metadata["dockerfile_exists"] is True
    assert result.metadata["readable"] is False
    assert result.metadata["read_error"] == (
        "PermissionError: access denied"
    )


def test_style_checker_skips_without_parseable_instructions(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "# syntax=docker/dockerfile:1\n"
        "# This Dockerfile has no instructions.\n"
        "\n",
    )

    result = DockerStyleChecker().run(tmp_path)[0]

    assert result.status == CheckStatus.SKIPPED
    assert result.should_block_release is False
    assert result.metadata["checked_instruction_count"] == 0
    assert result.metadata["parser_directive_count"] == 1
    assert result.metadata["mismatched_instructions"] == []


def test_style_checker_passes_uppercase_instructions(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "FROM python:3.11-slim\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        "RUN python -m pip install -r requirements.txt\n"
        'CMD ["python", "-m", "example"]\n',
    )

    result = DockerStyleChecker().run(tmp_path)[0]

    assert result.status == CheckStatus.PASSED
    assert result.risk_level == RiskLevel.INFO
    assert result.passed is True
    assert result.failed is False
    assert result.should_block_release is False
    assert result.rule_source == "Dockerfile reference"
    assert result.metadata["support_level"] == (
        "source-backed"
    )
    assert result.metadata["blocking_policy"] == "info"
    assert result.metadata["checked_instruction_count"] == 5
    assert result.metadata["mismatched_instruction_count"] == 0


def test_style_checker_warns_for_nonuppercase_instructions(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "from python:3.11-slim\n"
        "Workdir /app\n"
        "COPY . .\n",
    )

    result = DockerStyleChecker().run(tmp_path)[0]

    assert result.status == CheckStatus.WARNING
    assert result.risk_level == RiskLevel.LOW
    assert result.passed is False
    assert result.failed is False
    assert result.should_block_release is False
    assert result.recommendation is not None

    assert result.metadata["checked_instruction_count"] == 3
    assert result.metadata["mismatched_instruction_count"] == 2

    mismatches = result.metadata[
        "mismatched_instructions"
    ]

    assert mismatches[0]["original_keyword"] == "from"
    assert mismatches[0]["keyword"] == "FROM"
    assert mismatches[1]["original_keyword"] == "Workdir"
    assert mismatches[1]["keyword"] == "WORKDIR"

    assert any(
        "Dockerfile:1" in evidence
        and "found `from`" in evidence
        and "expected `FROM`" in evidence
        for evidence in result.evidence
    )


def test_style_checker_records_multiline_location_and_ignores_directives(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "# syntax=docker/dockerfile:1\n"
        "# Parser directives and comments are not instructions.\n"
        "rUn echo hello \\\n"
        "    && echo world\n",
    )

    result = DockerStyleChecker().run(tmp_path)[0]

    assert result.status == CheckStatus.WARNING
    assert result.risk_level == RiskLevel.LOW
    assert result.should_block_release is False

    assert result.metadata["parser_directive_count"] == 1
    assert result.metadata["checked_instruction_count"] == 1
    assert result.metadata["mismatched_instruction_count"] == 1

    mismatch = result.metadata[
        "mismatched_instructions"
    ][0]

    assert mismatch["original_keyword"] == "rUn"
    assert mismatch["keyword"] == "RUN"
    assert mismatch["start_line"] == 3
    assert mismatch["end_line"] == 4

    assert result.evidence == [
        (
            "Non-uppercase Docker instruction at "
            "Dockerfile:3-4: found `rUn`; expected `RUN`."
        )
    ]
