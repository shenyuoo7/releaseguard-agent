import re
from collections.abc import Iterable
from pathlib import Path

from releaseguard_agent.models.rule_evidence import RuleEvidence


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
    def from_file(cls, index_path: Path) -> "RuleIndexRetriever":
        """Load rules from a Markdown rule-index table."""
        normalized_path = Path(index_path)
        text = normalized_path.read_text(encoding="utf-8-sig")
        lines = text.splitlines()

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
