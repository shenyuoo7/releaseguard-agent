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
    assert record.to_dict()["rule_id"] == "RG-DOCKER-003"


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
