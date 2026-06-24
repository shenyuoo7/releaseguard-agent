from pathlib import Path

import pytest

from releaseguard_agent.rag.rule_index_retriever import (
    RuleIndexFormatError,
    RuleIndexRetriever,
    RuleNotFoundError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RULE_INDEX_PATH = (
    PROJECT_ROOT
    / "knowledge_base"
    / "release_rules"
    / "rule_index.md"
)

HEADER = (
    "| rule_id | rule_name | checker | source | support_level | "
    "priority | blocking_policy | evidence_type | phase |"
)

SEPARATOR = (
    "|---|---|---|---|---|---|---|---|---|"
)

SOURCE_HEADER = (
    "| rule_id | ReleaseGuard rule | support_level | blocking_policy | "
    "evidence_type | boundary |"
)

SOURCE_SEPARATOR = "|---|---|---|---|---|---|"


def _rule_row(
    rule_id: str = "RG-TEST-001",
    source: str = "pytest documentation",
) -> str:
    return (
        f"| {rule_id} | Tests directory exists | TestChecker | "
        f"{source} | source-backed | high | block | "
        "directory_exists | phase-1 |"
    )


def _index_text(*rows: str) -> str:
    return "\n".join((HEADER, SEPARATOR, *rows)) + "\n"


def _source_mapping_text(*, include_url: bool = True) -> str:
    metadata = (
        "# Test Source\n\n"
        "## Source\n\n"
    )

    if include_url:
        metadata += "- URL: https://example.com/test-source\n"

    metadata += (
        "- Type: test source\n\n"
        "## ReleaseGuard Rule Mapping\n\n"
    )

    return (
        metadata
        + "\n".join(
            (
                SOURCE_HEADER,
                SOURCE_SEPARATOR,
                (
                    "| RG-TEST-001 | Check tests directory | "
                    "source-backed | block | directory_exists | "
                    "Example rationale. |"
                ),
            )
        )
        + "\n"
    )


def test_loads_current_project_rule_index() -> None:
    retriever = RuleIndexRetriever.from_file(RULE_INDEX_PATH)

    assert len(retriever) == 37

    record = retriever.require("RG-DOCKER-003")

    assert record.rule_id == "RG-DOCKER-003"
    assert record.checker == "DockerChecker"
    assert record.support_level == "source-backed"
    assert record.blocking_policy == "block"
    assert record.knowledge_file == str(RULE_INDEX_PATH)
    assert record.line_number > 0
    assert len(record.source_documents) == 1

    source_document = record.source_documents[0]

    assert source_document.source_title == "Dockerfile Reference"
    assert source_document.source_url == (
        "https://docs.docker.com/reference/dockerfile/"
    )
    assert Path(source_document.knowledge_file).name == (
        "dockerfile_reference.md"
    )
    assert source_document.line_number > 0
    assert "parser directives" in source_document.rationale
    assert record.to_dict()["rule_id"] == "RG-DOCKER-003"
    assert record.to_dict()["source_documents"][0]["source_url"] == (
        "https://docs.docker.com/reference/dockerfile/"
    )


def test_loads_multiple_source_documents_for_one_rule() -> None:
    retriever = RuleIndexRetriever.from_file(RULE_INDEX_PATH)

    record = retriever.require("RG-SEC-002")
    source_titles = {
        source_document.source_title
        for source_document in record.source_documents
    }

    assert "Flask Debugging" in source_titles
    assert "Flask Quickstart" in source_titles
    assert len(record.source_documents) >= 2


def test_get_returns_none_for_unknown_rule() -> None:
    retriever = RuleIndexRetriever.from_file(RULE_INDEX_PATH)

    assert retriever.get("RG-UNKNOWN-999") is None


def test_require_raises_for_unknown_rule() -> None:
    retriever = RuleIndexRetriever.from_file(RULE_INDEX_PATH)

    with pytest.raises(
        RuleNotFoundError,
        match="RG-UNKNOWN-999",
    ):
        retriever.require("RG-UNKNOWN-999")


def test_loader_accepts_utf8_bom(tmp_path: Path) -> None:
    index_path = tmp_path / "rule_index.md"
    index_path.write_text(
        "\ufeff" + _index_text(_rule_row()),
        encoding="utf-8",
    )

    retriever = RuleIndexRetriever.from_file(index_path)

    assert retriever.require("RG-TEST-001").rule_name == (
        "Tests directory exists"
    )
    assert retriever.require("RG-TEST-001").source_documents == ()


def test_source_mapping_requires_source_url(tmp_path: Path) -> None:
    index_path = tmp_path / "rule_index.md"
    source_directory = tmp_path / "sources"
    source_directory.mkdir()
    index_path.write_text(
        _index_text(_rule_row()),
        encoding="utf-8",
    )
    (source_directory / "test_source.md").write_text(
        _source_mapping_text(include_url=False),
        encoding="utf-8",
    )

    with pytest.raises(
        RuleIndexFormatError,
        match="missing a source URL",
    ):
        RuleIndexRetriever.from_file(index_path)


def test_duplicate_rule_ids_raise_format_error(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "rule_index.md"
    index_path.write_text(
        _index_text(
            _rule_row(),
            _rule_row(),
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        RuleIndexFormatError,
        match="Duplicate rule_id",
    ):
        RuleIndexRetriever.from_file(index_path)


def test_malformed_header_raises_format_error(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "rule_index.md"
    malformed_header = HEADER.replace("phase", "owner")
    index_path.write_text(
        "\n".join(
            (
                malformed_header,
                SEPARATOR,
                _rule_row(),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        RuleIndexFormatError,
        match="header does not match",
    ):
        RuleIndexRetriever.from_file(index_path)


def test_missing_required_field_raises_format_error(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "rule_index.md"
    index_path.write_text(
        _index_text(_rule_row(source="")),
        encoding="utf-8",
    )

    with pytest.raises(
        RuleIndexFormatError,
        match="missing required fields: source",
    ):
        RuleIndexRetriever.from_file(index_path)
