import pytest

from releaseguard_agent.checkers.common.docker_checker import (
    DockerChecker,
)
from releaseguard_agent.models.check_result import (
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.scanners.dockerfile_scanner import (
    DockerfileScan,
)


RULE_IDS = [
    "RG-DOCKER-001",
    "RG-DOCKER-002",
    "RG-DOCKER-003",
    "RG-DOCKER-004",
    "RG-DOCKER-005",
    "RG-DOCKER-006",
    "RG-DOCKER-007",
]


def write_dockerfile(tmp_path, content):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(content, encoding="utf-8")
    return dockerfile


def write_compose(
    tmp_path,
    content,
    *,
    file_name="compose.yaml",
):
    compose_file = tmp_path / file_name
    compose_file.write_text(content, encoding="utf-8")
    return compose_file


def result_by_rule(results, rule_id):
    return next(
        result
        for result in results
        if result.rule_id == rule_id
    )


def complete_dockerfile(run_command=None):
    command = (
        run_command
        or "python -m pip install "
        "--no-cache-dir -r requirements.txt"
    )

    return (
        "FROM python:3.11-slim\n"
        "WORKDIR /app\n"
        "COPY requirements.txt .\n"
        f"RUN {command}\n"
        "COPY . .\n"
        'CMD ["python", "-m", "example"]\n'
    )


def test_checker_returns_seven_skipped_results_without_intent(
    tmp_path,
):
    results = DockerChecker().run(tmp_path)

    assert [result.rule_id for result in results] == RULE_IDS
    assert len(results) == 7
    assert all(
        result.status == CheckStatus.SKIPPED
        for result in results
    )
    assert all(
        result.should_block_release is False
        for result in results
    )


@pytest.mark.parametrize(
    "compose_content",
    [
        (
            "services:\n"
            "  web:\n"
            "    image: example/web:latest\n"
        ),
        (
            "services:\n"
            "  web:\n"
            "    build: ./service\n"
        ),
        (
            "services:\n"
            "  web:\n"
            "    build:\n"
            "      context: .\n"
            "      dockerfile: docker/Dockerfile\n"
        ),
        (
            "services:\n"
            "  web:\n"
            "    build:\n"
            "      context: .\n"
            "      dockerfile_inline: |\n"
            "        FROM alpine\n"
        ),
        (
            "services:\n"
            "  web:\n"
            "    build: ${BUILD_CONTEXT:-.}\n"
        ),
    ],
)
def test_checker_does_not_infer_root_intent_from_other_builds(
    tmp_path,
    compose_content,
):
    write_compose(tmp_path, compose_content)

    results = DockerChecker().run(tmp_path)

    existence = result_by_rule(
        results,
        "RG-DOCKER-001",
    )

    assert existence.status == CheckStatus.SKIPPED
    assert existence.should_block_release is False
    assert (
        existence.metadata["container_intent_detected"]
        is False
    )


@pytest.mark.parametrize(
    "compose_content",
    [
        (
            "services:\n"
            "  web:\n"
            "    build: .\n"
        ),
        (
            "services:\n"
            "  web:\n"
            "    build:\n"
            "      context: .\n"
        ),
        (
            "services:\n"
            "  web:\n"
            "    build:\n"
            "      dockerfile: Dockerfile\n"
        ),
    ],
)
def test_checker_blocks_missing_expected_root_dockerfile(
    tmp_path,
    compose_content,
):
    write_compose(tmp_path, compose_content)

    results = DockerChecker().run(tmp_path)
    existence = result_by_rule(
        results,
        "RG-DOCKER-001",
    )

    assert existence.status == CheckStatus.FAILED
    assert existence.risk_level == RiskLevel.HIGH
    assert existence.should_block_release is True
    assert (
        existence.metadata["container_intent_detected"]
        is True
    )
    assert len(existence.metadata["container_intent"]) == 1

    assert all(
        result.status == CheckStatus.SKIPPED
        for result in results[1:]
    )


def test_checker_handles_malformed_compose_conservatively(
    tmp_path,
):
    write_compose(
        tmp_path,
        "services:\n"
        "  web:\n"
        "    build: [\n",
    )

    results = DockerChecker().run(tmp_path)
    existence = result_by_rule(
        results,
        "RG-DOCKER-001",
    )

    assert existence.status == CheckStatus.SKIPPED
    assert existence.should_block_release is False
    assert existence.metadata["compose_diagnostics"]


def test_checker_passes_complete_dockerfile(tmp_path):
    write_dockerfile(tmp_path, complete_dockerfile())

    results = DockerChecker().run(tmp_path)

    assert [result.rule_id for result in results] == RULE_IDS
    assert len(results) == 7
    assert all(
        result.status == CheckStatus.PASSED
        for result in results
    )
    assert all(
        result.should_block_release is False
        for result in results
    )


def test_checker_blocks_dockerfile_without_from(tmp_path):
    write_dockerfile(
        tmp_path,
        "WORKDIR /app\n"
        "COPY requirements.txt .\n"
        "RUN python -m pip install -r requirements.txt\n"
        'CMD ["python", "-m", "example"]\n',
    )

    results = DockerChecker().run(tmp_path)

    from_result = result_by_rule(
        results,
        "RG-DOCKER-002",
    )
    position_result = result_by_rule(
        results,
        "RG-DOCKER-003",
    )

    assert from_result.status == CheckStatus.FAILED
    assert from_result.risk_level == RiskLevel.HIGH
    assert from_result.should_block_release is True
    assert position_result.status == CheckStatus.SKIPPED


def test_checker_blocks_instruction_before_first_from(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "WORKDIR /app\n"
        "FROM python:3.11-slim\n"
        "COPY requirements.txt .\n"
        "RUN python -m pip install -r requirements.txt\n"
        'CMD ["python", "-m", "example"]\n',
    )

    results = DockerChecker().run(tmp_path)
    position_result = result_by_rule(
        results,
        "RG-DOCKER-003",
    )

    assert position_result.status == CheckStatus.FAILED
    assert position_result.risk_level == RiskLevel.HIGH
    assert position_result.should_block_release is True
    assert (
        position_result.metadata[
            "invalid_pre_from_instructions"
        ][0]["keyword"]
        == "WORKDIR"
    )


def test_checker_blocks_parse_issue_before_first_from(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "not-an-instruction?\n"
        "FROM python:3.11-slim\n"
        "WORKDIR /app\n"
        "COPY requirements.txt .\n"
        "RUN python -m pip install -r requirements.txt\n"
        'CMD ["python", "-m", "example"]\n',
    )

    results = DockerChecker().run(tmp_path)
    position_result = result_by_rule(
        results,
        "RG-DOCKER-003",
    )

    assert position_result.status == CheckStatus.FAILED
    assert position_result.risk_level == RiskLevel.HIGH
    assert position_result.should_block_release is True

    issues = position_result.metadata[
        "invalid_pre_from_issues"
    ]

    assert len(issues) == 1
    assert issues[0]["line_number"] == 1
    assert any(
        "parse issue before FROM" in evidence
        for evidence in position_result.evidence
    )


def test_checker_allows_global_arg_before_from(tmp_path):
    write_dockerfile(
        tmp_path,
        "# syntax=docker/dockerfile:1\n"
        "ARG PYTHON_VERSION=3.11\n"
        "FROM python:${PYTHON_VERSION}-slim\n"
        "WORKDIR /app\n"
        "COPY requirements.txt .\n"
        "RUN python -m pip install -r requirements.txt\n"
        'CMD ["python", "-m", "example"]\n',
    )

    results = DockerChecker().run(tmp_path)
    position_result = result_by_rule(
        results,
        "RG-DOCKER-003",
    )

    assert position_result.status == CheckStatus.PASSED
    assert position_result.should_block_release is False
    assert (
        position_result.metadata[
            "pre_from_instructions"
        ][0]["keyword"]
        == "ARG"
    )


def test_checker_warns_for_missing_recommended_instructions(
    tmp_path,
):
    write_dockerfile(
        tmp_path,
        "FROM python:3.11-slim\n",
    )

    results = DockerChecker().run(tmp_path)

    assert result_by_rule(
        results,
        "RG-DOCKER-001",
    ).status == CheckStatus.PASSED

    assert result_by_rule(
        results,
        "RG-DOCKER-002",
    ).status == CheckStatus.PASSED

    assert result_by_rule(
        results,
        "RG-DOCKER-003",
    ).status == CheckStatus.PASSED

    for rule_id in (
        "RG-DOCKER-004",
        "RG-DOCKER-005",
        "RG-DOCKER-006",
        "RG-DOCKER-007",
    ):
        result = result_by_rule(results, rule_id)

        assert result.status == CheckStatus.WARNING
        assert result.risk_level == RiskLevel.MEDIUM
        assert result.should_block_release is False
        assert result.recommendation is not None


@pytest.mark.parametrize(
    "run_command",
    [
        "pip install -r requirements.txt",
        "pip3 install -r requirements.txt",
        "python3.11 -m pip install -r requirements.txt",
        "uv sync --frozen",
        "poetry install --only main",
        "pdm sync --prod",
    ],
)
def test_checker_detects_python_dependency_install_commands(
    tmp_path,
    run_command,
):
    write_dockerfile(
        tmp_path,
        complete_dockerfile(run_command),
    )

    result = result_by_rule(
        DockerChecker().run(tmp_path),
        "RG-DOCKER-006",
    )

    assert result.status == CheckStatus.PASSED
    assert result.metadata["matched_instructions"]
    assert run_command in result.evidence[0]


def test_checker_handles_unreadable_scan_result(tmp_path):
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

    results = DockerChecker(
        scanner=UnreadableScanner(),
    ).run(tmp_path)

    assert result_by_rule(
        results,
        "RG-DOCKER-001",
    ).status == CheckStatus.PASSED

    unreadable = result_by_rule(
        results,
        "RG-DOCKER-002",
    )

    assert unreadable.status == CheckStatus.FAILED
    assert unreadable.should_block_release is True

    assert all(
        result_by_rule(results, rule_id).status
        == CheckStatus.SKIPPED
        for rule_id in (
            "RG-DOCKER-003",
            "RG-DOCKER-004",
            "RG-DOCKER-005",
            "RG-DOCKER-006",
            "RG-DOCKER-007",
        )
    )


def test_checker_records_instruction_line_evidence(
    tmp_path,
):
    write_dockerfile(tmp_path, complete_dockerfile())

    results = DockerChecker().run(tmp_path)
    from_result = result_by_rule(
        results,
        "RG-DOCKER-002",
    )

    assert any(
        "Dockerfile:1" in item
        for item in from_result.evidence
    )
    assert (
        from_result.metadata["support_level"]
        == "source-backed"
    )
    assert (
        from_result.metadata["blocking_policy"]
        == "block"
    )
