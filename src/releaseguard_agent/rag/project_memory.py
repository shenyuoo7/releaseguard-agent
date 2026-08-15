"""Versioned project-memory sources and disposable local search cache."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Iterator

from releaseguard_agent.models.project_memory import (
    MemoryContext,
    MemoryContextSelection,
    MemoryKind,
    MemoryProvenance,
    MemoryQueryBudget,
    MemoryStatus,
    ProjectMemoryManifest,
    ProjectMemoryRecord,
    validate_project_memory_record_for_persistence,
)
from releaseguard_agent.runtime.tools import reject_sensitive_tool_arguments


_SCHEMA_VERSION = "1"
_RUNTIME_ROOT = Path(__file__).resolve().parents[3] / ".runtime"


class ProjectMemoryIntegrityError(ValueError):
    """Raised when a project-memory source or derived cache is not trustworthy."""


@dataclass(frozen=True)
class ProjectMemorySnapshot:
    manifest: ProjectMemoryManifest
    records: tuple[ProjectMemoryRecord, ...]
    tombstone_ids: tuple[str, ...]


class ProjectMemoryStore:
    """Atomically publish and fail-closed read immutable memory source versions."""

    def __init__(self, output_root: Path) -> None:
        self._output_root = _validate_output_root(output_root)

    @property
    def cache_root(self) -> Path:
        root = self._output_root / "cache"
        _validate_write_path(root, self._output_root, "cache root")
        return root

    def publish(
        self,
        project_id: str,
        records: Iterable[ProjectMemoryRecord],
        *,
        parent_memory_version: str | None = None,
    ) -> ProjectMemorySnapshot:
        """Publish a new immutable source version, retaining deletion tombstones."""

        _nonempty(project_id, "project_id")
        reject_sensitive_tool_arguments({"project_id": project_id})
        parent = (
            None
            if parent_memory_version is None
            else self.load(project_id, parent_memory_version)
        )
        prepared = _prepare_records(project_id, records)
        previous_ids = set() if parent is None else {item.memory_id for item in parent.records}
        tombstones = set() if parent is None else set(parent.tombstone_ids)
        tombstones.update(previous_ids - {item.memory_id for item in prepared})
        tombstone_ids = tuple(sorted(tombstones))
        records_sha256 = _sha256(_canonical_bytes([_record_preimage(item) for item in prepared]))
        tombstones_sha256 = _sha256(_canonical_bytes(list(tombstone_ids)))
        content_sha256 = _content_sha256(
            project_id=project_id,
            parent_memory_version=(None if parent is None else parent.manifest.memory_version),
            records_sha256=records_sha256,
            tombstones_sha256=tombstones_sha256,
        )
        memory_version = f"pm-{content_sha256}"
        final_records = tuple(
            replace(item, memory_version=memory_version) for item in prepared
        )
        manifest = ProjectMemoryManifest(
            schema_version=_SCHEMA_VERSION,
            project_id=project_id,
            memory_version=memory_version,
            parent_memory_version=(None if parent is None else parent.manifest.memory_version),
            records_sha256=records_sha256,
            tombstones_sha256=tombstones_sha256,
            created_at_utc=_content_created_at_utc(content_sha256),
            content_sha256=content_sha256,
        )
        snapshot = ProjectMemorySnapshot(manifest, final_records, tombstone_ids)
        _validate_snapshot(snapshot, parent)
        version_root = self._output_root / memory_version
        with _publication_lock(self._output_root):
            if version_root.exists():
                existing = self.load(project_id, memory_version)
                if existing != snapshot:
                    raise ProjectMemoryIntegrityError(
                        "memory version already has conflicting source"
                    )
                return existing
            self._reject_additional_successor(project_id, parent)
            _publish_snapshot(self._output_root, snapshot)
        return self.load(project_id, memory_version)

    def _reject_additional_successor(
        self,
        project_id: str,
        parent: ProjectMemorySnapshot | None,
    ) -> None:
        if parent is None:
            return
        if self._published_children(project_id, parent.manifest.memory_version):
            raise ProjectMemoryIntegrityError(
                "memory parent already has a published successor"
            )

    def _published_children(
        self,
        project_id: str,
        parent_memory_version: str,
    ) -> tuple[ProjectMemorySnapshot, ...]:
        try:
            entries = tuple(self._output_root.iterdir())
        except OSError as error:
            raise ProjectMemoryIntegrityError(
                "memory source root cannot be enumerated"
            ) from error
        children: list[ProjectMemorySnapshot] = []
        for entry in entries:
            if not entry.name.startswith("pm-"):
                continue
            _validate_memory_version(entry.name)
            version_root = _resolve_contained_path(
                entry,
                self._output_root,
                "published memory version directory",
            )
            if not version_root.is_dir():
                raise ProjectMemoryIntegrityError(
                    "published memory version path is not a directory"
                )
            manifest = _manifest_from_bytes(
                _read_artifact(version_root / "manifest.json", version_root)
            )
            if manifest.project_id != project_id:
                continue
            snapshot = self.load(project_id, manifest.memory_version)
            if snapshot.manifest.parent_memory_version == parent_memory_version:
                children.append(snapshot)
        return tuple(children)

    def delete(
        self,
        project_id: str,
        memory_id: str,
        parent_memory_version: str,
    ) -> ProjectMemorySnapshot:
        """Create a child source version with an explicit tombstone."""

        _nonempty(memory_id, "memory_id")
        parent = self.load(project_id, parent_memory_version)
        if memory_id not in {item.memory_id for item in parent.records}:
            raise ProjectMemoryIntegrityError("memory deletion target does not exist")
        remaining = tuple(item for item in parent.records if item.memory_id != memory_id)
        pending = tuple(replace(item, memory_version="pm-pending") for item in remaining)
        return self.publish(
            project_id,
            pending,
            parent_memory_version=parent.manifest.memory_version,
        )

    def load(self, project_id: str, memory_version: str) -> ProjectMemorySnapshot:
        """Return only a complete, canonical, parent-validated source version."""

        _nonempty(project_id, "project_id")
        _validate_memory_version(memory_version)
        return self._load(project_id, memory_version, seen=())

    def _load(
        self,
        project_id: str,
        memory_version: str,
        *,
        seen: tuple[str, ...],
    ) -> ProjectMemorySnapshot:
        if memory_version in seen:
            raise ProjectMemoryIntegrityError("memory parent cycle detected")
        version_root = _resolve_contained_path(
            self._output_root / memory_version,
            self._output_root,
            "version directory",
        )
        if not version_root.is_dir():
            raise ProjectMemoryIntegrityError("memory version path is not a directory")
        manifest = _manifest_from_bytes(_read_artifact(version_root / "manifest.json", version_root))
        records = _records_from_bytes(_read_artifact(version_root / "records.json", version_root))
        tombstones = _tombstones_from_bytes(
            _read_artifact(version_root / "tombstones.json", version_root)
        )
        markdown = _read_artifact(version_root / "records.md", version_root)
        if markdown != _markdown_bytes(records, tombstones, manifest):
            raise ProjectMemoryIntegrityError("human-readable memory source is incoherent")
        if manifest.project_id != project_id or manifest.memory_version != memory_version:
            raise ProjectMemoryIntegrityError("memory project or version does not match source path")
        parent = None
        if manifest.parent_memory_version is not None:
            parent = self._load(
                project_id,
                manifest.parent_memory_version,
                seen=(*seen, memory_version),
            )
        snapshot = ProjectMemorySnapshot(manifest, records, tombstones)
        _validate_snapshot(snapshot, parent)
        return snapshot


class ProjectMemoryIndex:
    """A disposable SQLite candidate index rebuilt exclusively from verified source."""

    def __init__(self, store: ProjectMemoryStore) -> None:
        self._store = store

    def rebuild(self, project_id: str, memory_version: str) -> Path:
        snapshot = self._store.load(project_id, memory_version)
        cache_root = self._store.cache_root
        _validate_write_path(cache_root, self._store.cache_root.parent, "cache root")
        cache_root.mkdir(parents=True, exist_ok=True)
        cache_path = cache_root / f"{memory_version}.sqlite3"
        _validate_write_path(cache_path, cache_root, "cache artifact")
        if cache_path.exists():
            _resolve_contained_path(cache_path, cache_root, "cache artifact")
            cache_path.unlink()
        connection = sqlite3.connect(cache_path)
        try:
            connection.executescript(
                """
                CREATE TABLE cache_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE memory_candidates(
                    memory_id TEXT PRIMARY KEY,
                    content_sha256 TEXT NOT NULL,
                    content TEXT NOT NULL
                );
                """
            )
            metadata = {
                "project_id": project_id,
                "memory_version": memory_version,
                "content_sha256": snapshot.manifest.content_sha256,
            }
            connection.executemany(
                "INSERT INTO cache_metadata VALUES (?, ?)", metadata.items()
            )
            connection.executemany(
                "INSERT INTO memory_candidates VALUES (?, ?, ?)",
                [
                    (item.memory_id, _sha256(item.content.encode("utf-8")), item.content)
                    for item in snapshot.records
                ],
            )
            connection.commit()
        finally:
            connection.close()
        return cache_path

    def records(self, project_id: str, memory_version: str) -> tuple[ProjectMemoryRecord, ...]:
        snapshot = self._store.load(project_id, memory_version)
        self._ensure_cache(snapshot)
        return snapshot.records

    def search(
        self,
        project_id: str,
        memory_version: str,
        query: str,
    ) -> tuple[ProjectMemoryRecord, ...]:
        snapshot = self._store.load(project_id, memory_version)
        cache_path = self._ensure_cache(snapshot)
        tokens = _query_tokens(query)
        connection = sqlite3.connect(cache_path)
        try:
            rows = connection.execute(
                "SELECT memory_id, content, content_sha256 FROM memory_candidates"
            ).fetchall()
        finally:
            connection.close()
        source_by_id = {item.memory_id: item for item in snapshot.records}
        scores: list[tuple[int, str]] = []
        for memory_id, content, content_sha256 in rows:
            source = source_by_id.get(memory_id)
            if not isinstance(memory_id, str) or source is None:
                continue
            if content != source.content or content_sha256 != _sha256(source.content.encode("utf-8")):
                return self.search(project_id, memory_version, query) if self._rebuild_after_corruption(cache_path, snapshot) else ()
            scores.append((sum(token in source.content.lower() for token in tokens), memory_id))
        return tuple(
            source_by_id[memory_id]
            for _, memory_id in sorted(scores, key=lambda item: (-item[0], item[1]))
            if _ > 0
        )

    def _ensure_cache(self, snapshot: ProjectMemorySnapshot) -> Path:
        cache_path = self._store.cache_root / f"{snapshot.manifest.memory_version}.sqlite3"
        try:
            if not cache_path.exists() or not _cache_matches(cache_path, snapshot):
                return self.rebuild(snapshot.manifest.project_id, snapshot.manifest.memory_version)
            return cache_path
        except (OSError, sqlite3.DatabaseError, ProjectMemoryIntegrityError):
            return self.rebuild(snapshot.manifest.project_id, snapshot.manifest.memory_version)

    def _rebuild_after_corruption(
        self,
        cache_path: Path,
        snapshot: ProjectMemorySnapshot,
    ) -> bool:
        if cache_path.exists():
            _resolve_contained_path(cache_path, self._store.cache_root, "cache artifact")
            cache_path.unlink()
        self.rebuild(snapshot.manifest.project_id, snapshot.manifest.memory_version)
        return True


class MemoryContextAssembler:
    """Produce a stable, bounded context from verified project-memory records."""

    def __init__(self, index: ProjectMemoryIndex) -> None:
        self._index = index

    def select(
        self,
        *,
        project_id: str,
        memory_version: str,
        query: str,
        active_run_references: Iterable[str],
        budget: MemoryQueryBudget,
    ) -> MemoryContext:
        references = tuple(active_run_references)
        reject_sensitive_tool_arguments(
            {"query": query, "active_run_references": list(references)}
        )
        records = self._index.records(project_id, memory_version)
        active_references = frozenset(references)
        ranked = sorted(
            ((_score(record, query, active_references), record) for record in records),
            key=lambda item: (-item[0], item[1].memory_id),
        )
        selected: list[MemoryContextSelection] = []
        omitted: list[MemoryContextSelection] = []
        accepted_content: set[str] = set()
        content_parts: list[str] = []
        character_count = 0
        token_count = 0
        now = datetime.now(UTC)
        for score, record in ranked:
            exclusion = _exclusion_reason(record, now)
            if exclusion is not None:
                omitted.append(MemoryContextSelection(record.memory_id, score, None, exclusion))
                continue
            canonical_content = record.content.casefold()
            if canonical_content in accepted_content:
                omitted.append(MemoryContextSelection(record.memory_id, score, None, "duplicate_content"))
                continue
            additional_characters = len(record.content) + (2 if content_parts else 0)
            additional_tokens = len(record.content.split())
            if len(selected) >= budget.top_k:
                reason = "top_k_budget"
            elif character_count + additional_characters > budget.max_characters:
                reason = "character_budget"
            elif token_count + additional_tokens > budget.max_tokens:
                reason = "token_budget"
            else:
                accepted_content.add(canonical_content)
                content_parts.append(record.content)
                character_count += additional_characters
                token_count += additional_tokens
                selected.append(MemoryContextSelection(record.memory_id, score, "ranked_relevant", None))
                continue
            omitted.append(MemoryContextSelection(record.memory_id, score, None, reason))
        return MemoryContext(
            project_id=project_id,
            memory_version=memory_version,
            selected=tuple(selected),
            omitted=tuple(omitted),
            content="\n\n".join(content_parts),
            character_count=character_count,
            token_count=token_count,
        )


def _prepare_records(
    project_id: str, records: Iterable[ProjectMemoryRecord]
) -> tuple[ProjectMemoryRecord, ...]:
    candidates = tuple(records)
    for item in candidates:
        validate_project_memory_record_for_persistence(item)
    prepared = tuple(sorted(candidates, key=lambda item: item.memory_id))
    if any(item.project_id != project_id for item in prepared):
        raise ValueError("memory records must belong to the published project")
    if any(item.memory_version != "pm-pending" for item in prepared):
        raise ValueError("memory records must not predeclare a source version")
    identifiers = tuple(item.memory_id for item in prepared)
    if identifiers != tuple(sorted(set(identifiers))):
        raise ValueError("memory record identifiers must be unique")
    return prepared


def _validate_snapshot(
    snapshot: ProjectMemorySnapshot,
    parent: ProjectMemorySnapshot | None,
) -> None:
    manifest = snapshot.manifest
    if manifest.schema_version != _SCHEMA_VERSION:
        raise ProjectMemoryIntegrityError("unsupported memory schema")
    _nonempty(manifest.project_id, "manifest project_id")
    _validate_memory_version(manifest.memory_version)
    if manifest.memory_version != f"pm-{manifest.content_sha256}":
        raise ProjectMemoryIntegrityError("memory version does not bind content identity")
    if not all(_is_sha256(value) for value in (manifest.records_sha256, manifest.tombstones_sha256, manifest.content_sha256)):
        raise ProjectMemoryIntegrityError("memory source digest is invalid")
    if manifest.created_at_utc != _content_created_at_utc(manifest.content_sha256):
        raise ProjectMemoryIntegrityError("memory creation time is not deterministic")
    if parent is None and manifest.parent_memory_version is not None:
        raise ProjectMemoryIntegrityError("memory parent source is unavailable")
    if parent is not None and manifest.parent_memory_version != parent.manifest.memory_version:
        raise ProjectMemoryIntegrityError("memory parent source does not match")
    records = snapshot.records
    if tuple(item.memory_id for item in records) != tuple(sorted(item.memory_id for item in records)):
        raise ProjectMemoryIntegrityError("memory records are not sorted")
    if len({item.memory_id for item in records}) != len(records):
        raise ProjectMemoryIntegrityError("memory records are not unique")
    if any(item.project_id != manifest.project_id or item.memory_version != manifest.memory_version for item in records):
        raise ProjectMemoryIntegrityError("memory record scope or version is invalid")
    if any(item.memory_id in snapshot.tombstone_ids for item in records):
        raise ProjectMemoryIntegrityError("active source record is also tombstoned")
    if tuple(sorted(set(snapshot.tombstone_ids))) != snapshot.tombstone_ids:
        raise ProjectMemoryIntegrityError("memory tombstones are not sorted and unique")
    if parent is None:
        if snapshot.tombstone_ids:
            raise ProjectMemoryIntegrityError("initial memory source cannot contain tombstones")
    else:
        parent_by_id = {item.memory_id: item for item in parent.records}
        current_by_id = {item.memory_id: item for item in records}
        parent_ids = set(parent_by_id)
        current_ids = set(current_by_id)
        expected_tombstone_ids = tuple(
            sorted(set(parent.tombstone_ids) | (parent_ids - current_ids))
        )
        if snapshot.tombstone_ids != expected_tombstone_ids:
            raise ProjectMemoryIntegrityError("memory tombstone chain is invalid")
        superseded_ids = tuple(
            item.supersedes for item in records if item.supersedes is not None
        )
        if len(superseded_ids) != len(set(superseded_ids)):
            raise ProjectMemoryIntegrityError(
                "memory supersedes relation must be one-to-one"
            )
        if any(
            item.supersedes is not None
            and (
                item.supersedes not in parent_by_id
                or parent_by_id[item.supersedes].status is not MemoryStatus.ACTIVE
            )
            for item in records
        ):
            raise ProjectMemoryIntegrityError("memory supersedes reference is not in parent")
        for item in records:
            if item.supersedes is None:
                continue
            replaced = current_by_id.get(item.supersedes)
            if replaced is not None and replaced.status is MemoryStatus.ACTIVE:
                raise ProjectMemoryIntegrityError(
                    "memory supersedes target must be tombstoned or transitioned"
                )
            if replaced is not None and not _is_supersession_transition(
                parent_by_id[item.supersedes], replaced
            ):
                raise ProjectMemoryIntegrityError(
                    "memory supersedes transition is invalid"
                )
        for memory_id in parent_ids & current_ids:
            parent_record = parent_by_id[memory_id]
            current_record = current_by_id[memory_id]
            if _record_preimage(parent_record) == _record_preimage(current_record):
                continue
            if _is_supersession_transition(parent_record, current_record):
                if not any(item.supersedes == memory_id for item in records):
                    raise ProjectMemoryIntegrityError(
                        "memory transition requires a replacement supersedes reference"
                    )
                continue
            raise ProjectMemoryIntegrityError("memory records are immutable by ID")
    expected_records = _sha256(_canonical_bytes([_record_preimage(item) for item in records]))
    expected_tombstones = _sha256(_canonical_bytes(list(snapshot.tombstone_ids)))
    if (expected_records, expected_tombstones) != (manifest.records_sha256, manifest.tombstones_sha256):
        raise ProjectMemoryIntegrityError("memory source digests do not match")
    expected_content = _content_sha256(
        project_id=manifest.project_id,
        parent_memory_version=manifest.parent_memory_version,
        records_sha256=manifest.records_sha256,
        tombstones_sha256=manifest.tombstones_sha256,
    )
    if expected_content != manifest.content_sha256:
        raise ProjectMemoryIntegrityError("memory source identity does not match")


def _publish_snapshot(output_root: Path, snapshot: ProjectMemorySnapshot) -> None:
    _validate_write_path(output_root, _RUNTIME_ROOT, "memory output root")
    output_root.mkdir(parents=True, exist_ok=True)
    _validate_write_path(output_root, _RUNTIME_ROOT, "memory output root")
    final_path = output_root / snapshot.manifest.memory_version
    _validate_write_path(final_path, output_root, "memory version directory")
    temporary_path = Path(tempfile.mkdtemp(prefix=".project-memory-", dir=output_root))
    try:
        _validate_write_path(temporary_path, output_root, "memory staging directory")
        _write_artifact(
            temporary_path / "manifest.json",
            _canonical_bytes(snapshot.manifest.to_dict()),
            temporary_path,
        )
        _write_artifact(
            temporary_path / "records.json",
            _canonical_bytes([item.to_dict() for item in snapshot.records]),
            temporary_path,
        )
        _write_artifact(
            temporary_path / "tombstones.json",
            _canonical_bytes(list(snapshot.tombstone_ids)),
            temporary_path,
        )
        _write_artifact(
            temporary_path / "records.md",
            _markdown_bytes(snapshot.records, snapshot.tombstone_ids, snapshot.manifest),
            temporary_path,
        )
        try:
            os.replace(temporary_path, final_path)
        except FileExistsError as error:
            raise ProjectMemoryIntegrityError("memory source version already exists") from error
    finally:
        if temporary_path.exists():
            shutil.rmtree(temporary_path)


@contextmanager
def _publication_lock(output_root: Path) -> Iterator[None]:
    _validate_write_path(output_root, _RUNTIME_ROOT, "memory output root")
    output_root.mkdir(parents=True, exist_ok=True)
    lock_path = output_root / ".project-memory-publish.lock"
    _validate_write_path(lock_path, output_root, "memory publication lock")
    try:
        os.mkdir(lock_path)
    except FileExistsError as error:
        raise ProjectMemoryIntegrityError("memory publication is already in progress") from error
    try:
        yield
    finally:
        if lock_path.exists():
            _resolve_contained_path(lock_path, output_root, "memory publication lock")
            try:
                lock_path.rmdir()
            except OSError as error:
                raise ProjectMemoryIntegrityError(
                    "memory publication lock cannot be released"
                ) from error


def _cache_matches(path: Path, snapshot: ProjectMemorySnapshot) -> bool:
    _resolve_contained_path(path, path.parent, "cache artifact")
    connection = sqlite3.connect(path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            return False
        metadata = dict(connection.execute("SELECT key, value FROM cache_metadata"))
        if metadata != {
            "project_id": snapshot.manifest.project_id,
            "memory_version": snapshot.manifest.memory_version,
            "content_sha256": snapshot.manifest.content_sha256,
        }:
            return False
        cached = connection.execute(
            "SELECT memory_id, content_sha256, content FROM memory_candidates ORDER BY memory_id"
        ).fetchall()
        expected = [
            (item.memory_id, _sha256(item.content.encode("utf-8")), item.content)
            for item in snapshot.records
        ]
        return cached == expected
    finally:
        connection.close()


def _read_artifact(path: Path, version_root: Path) -> bytes:
    resolved = _resolve_contained_path(path, version_root, "source artifact")
    if not resolved.is_file():
        raise ProjectMemoryIntegrityError("required memory source artifact is unavailable")
    try:
        return resolved.read_bytes()
    except OSError as error:
        raise ProjectMemoryIntegrityError(
            "required memory source artifact is unavailable"
        ) from error


def _write_artifact(path: Path, raw: bytes, version_root: Path) -> None:
    resolved = _validate_write_path(path, version_root, "source artifact")
    try:
        resolved.write_bytes(raw)
    except OSError as error:
        raise ProjectMemoryIntegrityError("memory source artifact cannot be written") from error


def _manifest_from_bytes(raw: bytes) -> ProjectMemoryManifest:
    value = _canonical_json_value(raw, "manifest")
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "project_id", "memory_version", "parent_memory_version",
        "records_sha256", "tombstones_sha256", "created_at_utc", "content_sha256",
    }:
        raise ProjectMemoryIntegrityError("memory manifest is invalid")
    try:
        parent = value["parent_memory_version"]
        if parent is not None and not isinstance(parent, str):
            raise TypeError("parent")
        return ProjectMemoryManifest(
            **{key: value[key] for key in value if key != "parent_memory_version"},
            parent_memory_version=parent,
        )
    except (TypeError, ValueError) as error:
        raise ProjectMemoryIntegrityError("memory manifest is invalid") from error


def _records_from_bytes(raw: bytes) -> tuple[ProjectMemoryRecord, ...]:
    value = _canonical_json_value(raw, "records")
    if not isinstance(value, list):
        raise ProjectMemoryIntegrityError("memory records are invalid")
    try:
        expected = {
            "memory_id", "project_id", "kind", "content", "provenance", "created_at_utc",
            "updated_at_utc", "status", "confidence", "supersedes", "expires_at_utc", "memory_version",
        }
        records: list[ProjectMemoryRecord] = []
        for item in value:
            if not isinstance(item, dict) or set(item) != expected:
                raise TypeError("record")
            provenance = item["provenance"]
            if not isinstance(provenance, dict) or set(provenance) != {
                "run_id", "event_id", "evidence_id", "rule_id", "human_correction_id"
            }:
                raise TypeError("provenance")
            records.append(ProjectMemoryRecord(
                memory_id=item["memory_id"], project_id=item["project_id"],
                kind=MemoryKind(item["kind"]), content=item["content"],
                provenance=MemoryProvenance(**provenance), created_at_utc=item["created_at_utc"],
                updated_at_utc=item["updated_at_utc"], status=MemoryStatus(item["status"]),
                confidence=item["confidence"], supersedes=item["supersedes"],
                expires_at_utc=item["expires_at_utc"], memory_version=item["memory_version"],
            ))
        return tuple(records)
    except (KeyError, TypeError, ValueError) as error:
        raise ProjectMemoryIntegrityError("memory record is invalid") from error


def _tombstones_from_bytes(raw: bytes) -> tuple[str, ...]:
    value = _canonical_json_value(raw, "tombstones")
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ProjectMemoryIntegrityError("memory tombstones are invalid")
    result = tuple(value)
    if result != tuple(sorted(set(result))):
        raise ProjectMemoryIntegrityError("memory tombstones are invalid")
    return result


def _canonical_json_value(raw: bytes, label: str) -> object:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectMemoryIntegrityError(f"memory {label} JSON is invalid") from error
    if _canonical_bytes(value) != raw:
        raise ProjectMemoryIntegrityError(f"memory {label} JSON is not canonical")
    return value


def _record_preimage(record: ProjectMemoryRecord) -> dict[str, object]:
    value = record.to_dict()
    value.pop("memory_version")
    return value


def _is_supersession_transition(
    parent_record: ProjectMemoryRecord,
    current_record: ProjectMemoryRecord,
) -> bool:
    if (
        parent_record.status is not MemoryStatus.ACTIVE
        or current_record.status is not MemoryStatus.SUPERSEDED
    ):
        return False
    parent_value = _record_preimage(parent_record)
    current_value = _record_preimage(current_record)
    parent_value.pop("status")
    current_value.pop("status")
    return parent_value == current_value


def _content_sha256(*, project_id: str, parent_memory_version: str | None, records_sha256: str, tombstones_sha256: str) -> str:
    return _sha256(_canonical_bytes({
        "schema_version": _SCHEMA_VERSION, "project_id": project_id,
        "parent_memory_version": parent_memory_version, "records_sha256": records_sha256,
        "tombstones_sha256": tombstones_sha256,
    }))


def _markdown_bytes(records: tuple[ProjectMemoryRecord, ...], tombstones: tuple[str, ...], manifest: ProjectMemoryManifest) -> bytes:
    lines = ["# Project Memory", "", f"Project: {manifest.project_id}", f"Version: {manifest.memory_version}", "", "## Records", ""]
    for record in records:
        lines.extend((f"### {record.memory_id}", "", record.content, ""))
    lines.extend(("## Tombstones", "", *(f"- {item}" for item in tombstones), ""))
    return "\n".join(lines).encode("utf-8")


def _exclusion_reason(record: ProjectMemoryRecord, now: datetime) -> str | None:
    if record.status is not MemoryStatus.ACTIVE:
        return record.status.value.lower()
    if record.expires_at_utc is not None and datetime.fromisoformat(record.expires_at_utc) <= now:
        return "expired"
    return None


def _score(record: ProjectMemoryRecord, query: str, references: frozenset[str]) -> float:
    provenance_values = frozenset(value for value in record.provenance.to_dict().values() if value)
    active_matches = len(provenance_values & references)
    terms = _query_tokens(query)
    term_matches = sum(term in record.content.casefold() for term in terms)
    return float(active_matches * 100 + term_matches)


def _query_tokens(query: str) -> tuple[str, ...]:
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    return tuple(sorted({part for part in query.casefold().split() if part}))


def _validate_output_root(output_root: Path) -> Path:
    candidate = Path(output_root).expanduser().resolve()
    runtime = _RUNTIME_ROOT.resolve()
    try:
        candidate.relative_to(runtime)
    except ValueError as error:
        raise ProjectMemoryIntegrityError("memory output root must remain under the configured runtime root") from error
    return candidate


def _validate_write_path(path: Path, allowed_root: Path, label: str) -> Path:
    candidate = Path(path).expanduser().resolve()
    allowed = Path(allowed_root).expanduser().resolve()
    runtime = _RUNTIME_ROOT.resolve()
    try:
        candidate.relative_to(runtime)
        candidate.relative_to(allowed)
    except ValueError as error:
        raise ProjectMemoryIntegrityError(f"memory {label} escapes the configured runtime root") from error
    return candidate


def _resolve_contained_path(path: Path, allowed_root: Path, label: str) -> Path:
    try:
        candidate = Path(path).resolve(strict=True)
    except OSError as error:
        raise ProjectMemoryIntegrityError(f"required memory {label} is unavailable") from error
    return _validate_write_path(candidate, allowed_root, label)


def _validate_memory_version(value: str) -> None:
    if not isinstance(value, str) or len(value) != 67 or not value.startswith("pm-") or not _is_sha256(value[3:]):
        raise ProjectMemoryIntegrityError("memory version is invalid")


def _content_created_at_utc(content_sha256: str) -> str:
    return datetime.fromtimestamp(int(content_sha256[:8], 16), UTC).isoformat()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
