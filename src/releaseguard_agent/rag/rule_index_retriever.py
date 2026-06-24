import re
from collections.abc import Iterable
from pathlib import Path

from releaseguard_agent.models.rule_evidence import (
    RuleEvidence,
    RuleSourceEvidence,
)


_REQUIRED_COLUMNS = (
    "rule_id",
    "rule_name",
    "checker",
    "source",
    "support_level",
    "priority",
    "blocking_policy",
    "evidence_type",
    "phase",
)

_VALID_SUPPORT_LEVELS = {
    "source-backed",
    "releaseguard-default",
    "needs-source-mapping",
}

_VALID_PRIORITIES = {
    "low",
    "medium",
    "high",
    "critical",
}

_VALID_BLOCKING_POLICIES = {
    "block",
    "warn",
    "info",
    "conditional",
}

_RULE_ID_PATTERN = re.compile(r"RG-[A-Z]+-\d{3}")


class RuleIndexFormatError(ValueError):
    """Raised when the rule index does not follow its required format."""


class RuleNotFoundError(LookupError):
    """Raised when a required rule ID is not present."""


class RuleIndexRetriever:
    """Load and retrieve structured rules from the global rule index."""

    def __init__(self, records: Iterable[RuleEvidence]) -> None:
        ordered_records = tuple(records)
        records_by_id: dict[str, RuleEvidence] = {}

        for record in ordered_records:
            existing = records_by_id.get(record.rule_id)

            if existing is not None:
                raise RuleIndexFormatError(
                    f"Duplicate rule_id {record.rule_id!r} at "
                    f"{record.knowledge_file}:{record.line_number}; "
                    f"first defined at "
                    f"{existing.knowledge_file}:{existing.line_number}."
                )

            records_by_id[record.rule_id] = record

        self._records = ordered_records
        self._records_by_id = records_by_id

    @classmethod
    def from_file(
        cls,
        index_path: Path,
        source_directory: Path | None = None,
    ) -> "RuleIndexRetriever":
        """Load rules from a Markdown rule-index table."""
        normalized_path = Path(index_path)
        text = normalized_path.read_text(encoding="utf-8-sig")
        lines = text.splitlines()
        source_evidence_by_rule_id = _load_source_evidence(
            normalized_path.parent / "sources"
            if source_directory is None
            else source_directory
        )

        header_index = _find_header_index(lines)
        headers = _split_markdown_row(lines[header_index])

        if headers != _REQUIRED_COLUMNS:
            raise RuleIndexFormatError(
                "Rule index header does not match the required columns. "
                f"Expected {_REQUIRED_COLUMNS}, found {headers}."
            )

        separator_index = header_index + 1

        if separator_index >= len(lines) or not _is_separator_row(
            lines[separator_index]
        ):
            raise RuleIndexFormatError(
                "Rule index table is missing a valid separator row."
            )

        records: list[RuleEvidence] = []

        for line_index in range(separator_index + 1, len(lines)):
            line = lines[line_index]
            stripped = line.strip()
            line_number = line_index + 1

            if not stripped:
                if records:
                    break

                continue

            if not stripped.startswith("|") or not stripped.endswith("|"):
                if records:
                    break

                continue

            cells = _split_markdown_row(line)

            if len(cells) != len(_REQUIRED_COLUMNS):
                raise RuleIndexFormatError(
                    f"Invalid rule table row at "
                    f"{normalized_path}:{line_number}."
                )

            values = dict(zip(_REQUIRED_COLUMNS, cells, strict=True))
            _validate_values(
                values=values,
                index_path=normalized_path,
                line_number=line_number,
            )

            records.append(
                RuleEvidence(
                    rule_id=values["rule_id"],
                    rule_name=values["rule_name"],
                    checker=values["checker"],
                    source=values["source"],
                    support_level=values["support_level"],
                    priority=values["priority"],
                    blocking_policy=values["blocking_policy"],
                    evidence_type=values["evidence_type"],
                    phase=values["phase"],
                    knowledge_file=str(normalized_path),
                    line_number=line_number,
                    source_documents=source_evidence_by_rule_id.get(
                        values["rule_id"],
                        (),
                    ),
                )
            )

        if not records:
            raise RuleIndexFormatError(
                f"No rule records were found in {normalized_path}."
            )

        return cls(records)

    @property
    def records(self) -> tuple[RuleEvidence, ...]:
        """Return all rules in their original index order."""
        return self._records

    def get(self, rule_id: str) -> RuleEvidence | None:
        """Return one rule or None when it is unknown."""
        return self._records_by_id.get(rule_id.strip())

    def require(self, rule_id: str) -> RuleEvidence:
        """Return one rule or raise an explicit not-found error."""
        normalized_rule_id = rule_id.strip()
        record = self.get(normalized_rule_id)

        if record is None:
            raise RuleNotFoundError(
                f"Rule ID {normalized_rule_id!r} was not found."
            )

        return record

    def __len__(self) -> int:
        return len(self._records)


def _find_header_index(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        cells = _split_markdown_row(line)

        if cells and cells[0] == "rule_id":
            return index

    raise RuleIndexFormatError(
        "Required rule index table header was not found."
    )


def _load_source_evidence(
    source_directory: Path,
) -> dict[str, tuple[RuleSourceEvidence, ...]]:
    normalized_source_directory = Path(source_directory)

    if not normalized_source_directory.exists():
        return {}

    if not normalized_source_directory.is_dir():
        raise RuleIndexFormatError(
            f"Rule source evidence path is not a directory: "
            f"{normalized_source_directory}."
        )

    documents_by_rule_id: dict[str, list[RuleSourceEvidence]] = {}

    for source_path in sorted(normalized_source_directory.glob("*.md")):
        for document in _load_source_file_evidence(source_path):
            documents_by_rule_id.setdefault(
                document.rule_id,
                [],
            ).append(document)

    return {
        rule_id: tuple(documents)
        for rule_id, documents in documents_by_rule_id.items()
    }


def _load_source_file_evidence(
    source_path: Path,
) -> tuple[RuleSourceEvidence, ...]:
    normalized_path = Path(source_path)
    lines = normalized_path.read_text(encoding="utf-8-sig").splitlines()
    source_title = _extract_title(lines, normalized_path)
    source_url = _extract_metadata_value(lines, "URL")
    source_type = _extract_metadata_value(lines, "Type")
    header_index = _find_source_mapping_header_index(lines)

    if header_index is None:
        return ()

    if not source_url:
        raise RuleIndexFormatError(
            f"Source mapping in {normalized_path} is missing a source URL."
        )

    separator_index = header_index + 1

    if separator_index >= len(lines) or not _is_source_separator_row(
        lines[separator_index]
    ):
        raise RuleIndexFormatError(
            f"Source mapping table in {normalized_path} is missing a "
            "valid separator row."
        )

    headers = tuple(
        _normalize_header(cell)
        for cell in _split_markdown_row(lines[header_index])
    )
    documents: list[RuleSourceEvidence] = []

    for line_index in range(separator_index + 1, len(lines)):
        line = lines[line_index]
        stripped = line.strip()
        line_number = line_index + 1

        if not stripped:
            break

        if not stripped.startswith("|") or not stripped.endswith("|"):
            break

        cells = _split_markdown_row(line)

        if len(cells) != len(headers):
            raise RuleIndexFormatError(
                f"Invalid source mapping row at "
                f"{normalized_path}:{line_number}."
            )

        values = dict(zip(headers, cells, strict=True))
        rule_id = values.get("rule_id", "")

        if _RULE_ID_PATTERN.fullmatch(rule_id) is None:
            raise RuleIndexFormatError(
                f"Invalid source mapping rule_id {rule_id!r} at "
                f"{normalized_path}:{line_number}."
            )

        rule_text = (
            values.get("releaseguard_rule")
            or values.get("rule")
            or ""
        )
        rationale = (
            values.get("boundary")
            or values.get("implementation_boundary")
            or values.get("policy_boundary")
            or rule_text
        )

        documents.append(
            RuleSourceEvidence(
                rule_id=rule_id,
                source_title=source_title,
                source_url=source_url,
                source_type=source_type,
                rule_text=rule_text,
                rationale=rationale,
                knowledge_file=str(normalized_path),
                line_number=line_number,
            )
        )

    return tuple(documents)


def _find_source_mapping_header_index(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        cells = _split_markdown_row(line)

        if cells and _normalize_header(cells[0]) == "rule_id":
            return index

    return None


def _extract_title(lines: list[str], source_path: Path) -> str:
    for line in lines:
        stripped = line.strip()

        if stripped.startswith("# "):
            return stripped[2:].strip()

    return source_path.stem


def _extract_metadata_value(lines: list[str], field_name: str) -> str:
    expected_prefix = f"- {field_name}:".lower()

    for line in lines:
        stripped = line.strip()

        if stripped.lower().startswith(expected_prefix):
            return stripped.split(":", 1)[1].strip()

    return ""


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _split_markdown_row(line: str) -> tuple[str, ...]:
    stripped = line.strip()

    if not stripped.startswith("|") or not stripped.endswith("|"):
        return ()

    return tuple(
        cell.strip()
        for cell in stripped[1:-1].split("|")
    )


def _is_separator_row(line: str) -> bool:
    cells = _split_markdown_row(line)

    return (
        len(cells) == len(_REQUIRED_COLUMNS)
        and all(
            re.fullmatch(r":?-{3,}:?", cell) is not None
            for cell in cells
        )
    )


def _is_source_separator_row(line: str) -> bool:
    cells = _split_markdown_row(line)

    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell) is not None
        for cell in cells
    )


def _validate_values(
    *,
    values: dict[str, str],
    index_path: Path,
    line_number: int,
) -> None:
    missing_fields = [
        field_name
        for field_name, value in values.items()
        if not value
    ]

    if missing_fields:
        raise RuleIndexFormatError(
            f"Rule at {index_path}:{line_number} has missing required "
            f"fields: {', '.join(missing_fields)}."
        )

    rule_id = values["rule_id"]

    if _RULE_ID_PATTERN.fullmatch(rule_id) is None:
        raise RuleIndexFormatError(
            f"Invalid rule_id {rule_id!r} at "
            f"{index_path}:{line_number}."
        )

    if values["support_level"] not in _VALID_SUPPORT_LEVELS:
        raise RuleIndexFormatError(
            f"Invalid support_level at {index_path}:{line_number}."
        )

    if values["priority"] not in _VALID_PRIORITIES:
        raise RuleIndexFormatError(
            f"Invalid priority at {index_path}:{line_number}."
        )

    if values["blocking_policy"] not in _VALID_BLOCKING_POLICIES:
        raise RuleIndexFormatError(
            f"Invalid blocking_policy at "
            f"{index_path}:{line_number}."
        )
