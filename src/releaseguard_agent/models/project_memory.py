"""Frozen contracts for transparent, versioned local project memory."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

from releaseguard_agent.runtime.tools import reject_sensitive_tool_arguments


class MemoryKind(str, Enum):
    PROJECT_FACT = "PROJECT_FACT"
    DECISION = "DECISION"
    CONSTRAINT = "CONSTRAINT"
    EVIDENCE_GAP = "EVIDENCE_GAP"
    RUN_LESSON = "RUN_LESSON"
    HUMAN_CORRECTION = "HUMAN_CORRECTION"


class MemoryStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    DISABLED = "DISABLED"
    EXPIRED = "EXPIRED"
    TOMBSTONED = "TOMBSTONED"


_MEMORY_VERSION = re.compile(r"pm-(?:pending|[0-9a-f]{64})")


@dataclass(frozen=True)
class MemoryProvenance:
    run_id: str | None
    event_id: str | None
    evidence_id: str | None
    rule_id: str | None
    human_correction_id: str | None

    def __post_init__(self) -> None:
        values = self.to_dict()
        reject_sensitive_tool_arguments(values)
        if not any(values.values()):
            raise ValueError("memory provenance is required")
        for name, value in values.items():
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string when supplied")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "run_id": self.run_id,
            "event_id": self.event_id,
            "evidence_id": self.evidence_id,
            "rule_id": self.rule_id,
            "human_correction_id": self.human_correction_id,
        }


@dataclass(frozen=True)
class ProjectMemoryRecord:
    memory_id: str
    project_id: str
    kind: MemoryKind
    content: str
    provenance: MemoryProvenance
    created_at_utc: str
    updated_at_utc: str
    status: MemoryStatus
    confidence: float
    supersedes: str | None
    expires_at_utc: str | None
    memory_version: str

    def __post_init__(self) -> None:
        _nonempty(self.memory_id, "memory_id")
        _nonempty(self.project_id, "project_id")
        if not isinstance(self.kind, MemoryKind):
            raise ValueError("kind is invalid")
        if not isinstance(self.status, MemoryStatus):
            raise ValueError("status is invalid")
        if not isinstance(self.provenance, MemoryProvenance):
            raise ValueError("provenance is invalid")
        normalized_content = _normalized_content(self.content)
        object.__setattr__(self, "content", normalized_content)
        reject_sensitive_tool_arguments(self.to_dict())
        _canonical_utc(self.created_at_utc, "created_at_utc")
        _canonical_utc(self.updated_at_utc, "updated_at_utc")
        if self.expires_at_utc is not None:
            _canonical_utc(self.expires_at_utc, "expires_at_utc")
        if not isinstance(self.confidence, (int, float)) or isinstance(
            self.confidence, bool
        ) or not 0 <= float(self.confidence) <= 1:
            raise ValueError("confidence must be between zero and one")
        if self.supersedes is not None:
            _nonempty(self.supersedes, "supersedes")
            if self.supersedes == self.memory_id:
                raise ValueError("supersedes cannot reference the same memory")
        if not isinstance(self.memory_version, str) or _MEMORY_VERSION.fullmatch(
            self.memory_version
        ) is None:
            raise ValueError("memory_version is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "project_id": self.project_id,
            "kind": self.kind.value,
            "content": self.content,
            "provenance": self.provenance.to_dict(),
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "status": self.status.value,
            "confidence": float(self.confidence),
            "supersedes": self.supersedes,
            "expires_at_utc": self.expires_at_utc,
            "memory_version": self.memory_version,
        }


def validate_project_memory_record_for_persistence(record: ProjectMemoryRecord) -> None:
    """Reapply frozen-record validation at the durable source boundary."""

    if not isinstance(record, ProjectMemoryRecord):
        raise ValueError("memory record must use the frozen record contract")
    record.__post_init__()
    reject_sensitive_tool_arguments(record.to_dict())


@dataclass(frozen=True)
class ProjectMemoryManifest:
    schema_version: str
    project_id: str
    memory_version: str
    parent_memory_version: str | None
    records_sha256: str
    tombstones_sha256: str
    created_at_utc: str
    content_sha256: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "memory_version": self.memory_version,
            "parent_memory_version": self.parent_memory_version,
            "records_sha256": self.records_sha256,
            "tombstones_sha256": self.tombstones_sha256,
            "created_at_utc": self.created_at_utc,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class MemoryQueryBudget:
    top_k: int
    max_characters: int
    max_tokens: int

    def __post_init__(self) -> None:
        for name, value in (
            ("top_k", self.top_k),
            ("max_characters", self.max_characters),
            ("max_tokens", self.max_tokens),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    def to_dict(self) -> dict[str, int]:
        return {
            "top_k": self.top_k,
            "max_characters": self.max_characters,
            "max_tokens": self.max_tokens,
        }


@dataclass(frozen=True)
class MemoryContextSelection:
    memory_id: str
    score: float
    inclusion_reason: str | None
    exclusion_reason: str | None

    def to_dict(self) -> dict[str, str | float | None]:
        return {
            "memory_id": self.memory_id,
            "score": self.score,
            "inclusion_reason": self.inclusion_reason,
            "exclusion_reason": self.exclusion_reason,
        }


@dataclass(frozen=True)
class MemoryContext:
    project_id: str
    memory_version: str
    selected: tuple[MemoryContextSelection, ...]
    omitted: tuple[MemoryContextSelection, ...]
    content: str
    character_count: int
    token_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "memory_version": self.memory_version,
            "selected": [item.to_dict() for item in self.selected],
            "omitted": [item.to_dict() for item in self.omitted],
            "content": self.content,
            "character_count": self.character_count,
            "token_count": self.token_count,
        }


def _normalized_content(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("content must be a non-empty string")
    content = " ".join(value.split())
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        decoded = None
    if _contains_raw_conversation(decoded):
        raise ValueError("raw run conversation content is not permitted")
    return content


def _contains_raw_conversation(value: object) -> bool:
    if isinstance(value, dict):
        normalized_keys = {
            re.sub(r"[^a-z0-9]", "", str(key).casefold()) for key in value
        }
        if {"role", "content"}.issubset(normalized_keys):
            return True
        if any(
            key in {
                "history",
                "messages",
                "conversation",
                "chat",
                "chathistory",
                "transcript",
                "turns",
            }
            or "prompt" in key
            for key in normalized_keys
        ):
            return True
        return any(_contains_raw_conversation(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_raw_conversation(item) for item in value)
    return False


def _nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _canonical_utc(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be canonical UTC") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be canonical UTC")
    canonical = parsed.astimezone(UTC).isoformat()
    if value not in {canonical, canonical.removesuffix("+00:00") + "Z"}:
        raise ValueError(f"{name} must be canonical UTC")
