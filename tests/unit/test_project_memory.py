from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from releaseguard_agent.models.project_memory import (
    MemoryKind,
    MemoryProvenance,
    MemoryQueryBudget,
    MemoryStatus,
    ProjectMemoryRecord,
)
from releaseguard_agent.rag.project_memory import (
    MemoryContextAssembler,
    ProjectMemoryIndex,
    ProjectMemoryIntegrityError,
    ProjectMemoryStore,
)
from releaseguard_agent.rag import project_memory as project_memory_module


def runtime_root(tmp_path: Path) -> Path:
    unique = hashlib.sha256(str(tmp_path).encode("utf-8")).hexdigest()[:16]
    root = Path.cwd() / ".runtime" / "pytest-project-memory-source" / unique
    root.mkdir(parents=True, exist_ok=True)
    return root


def record(
    memory_id: str = "memory-1",
    *,
    content: str = "Docker image must run as a non-root user.",
    kind: MemoryKind = MemoryKind.CONSTRAINT,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    supersedes: str | None = None,
    provenance: MemoryProvenance | None = None,
) -> ProjectMemoryRecord:
    return ProjectMemoryRecord(
        memory_id=memory_id,
        project_id="project-alpha",
        kind=kind,
        content=content,
        provenance=provenance
        or MemoryProvenance(
            run_id="run-1",
            event_id="event-1",
            evidence_id="evidence-1",
            rule_id="RULE-DOCKER-001",
            human_correction_id=None,
        ),
        created_at_utc="2026-08-15T00:00:00+00:00",
        updated_at_utc="2026-08-15T00:00:00+00:00",
        status=status,
        confidence=0.9,
        supersedes=supersedes,
        expires_at_utc=None,
        memory_version="pm-pending",
    )


def test_store_publishes_repeatable_isolated_human_readable_sources(
    tmp_path: Path,
) -> None:
    """Changing a root must not change the immutable source identity."""

    first = ProjectMemoryStore(runtime_root(tmp_path) / "one").publish(
        "project-alpha", (record(),)
    )
    second = ProjectMemoryStore(runtime_root(tmp_path) / "two").publish(
        "project-alpha", (record(),)
    )

    assert first.manifest.memory_version == second.manifest.memory_version
    assert first.records[0].memory_version == first.manifest.memory_version
    assert (runtime_root(tmp_path) / "one" / first.manifest.memory_version / "records.md").is_file()
    with pytest.raises(ProjectMemoryIntegrityError, match="project"):
        ProjectMemoryStore(runtime_root(tmp_path) / "one").load(
            "project-beta", first.manifest.memory_version
        )


def test_store_rejects_tampered_source_and_parent_chain(tmp_path: Path) -> None:
    """Source bytes and parent binding are verified before records are returned."""

    store = ProjectMemoryStore(runtime_root(tmp_path))
    first = store.publish("project-alpha", (record(),))
    second = store.publish(
        "project-alpha",
        (record("memory-2", supersedes="memory-1"),),
        parent_memory_version=first.manifest.memory_version,
    )
    manifest_path = runtime_root(tmp_path) / second.manifest.memory_version / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["parent_memory_version"] = None
    manifest_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(ProjectMemoryIntegrityError, match="identity|parent|tombstone"):
        store.load("project-alpha", second.manifest.memory_version)


def test_store_rejects_sensitive_and_invalid_values_before_publish(tmp_path: Path) -> None:
    """A secret-bearing record must not leave a source artifact behind."""

    store = ProjectMemoryStore(runtime_root(tmp_path))
    with pytest.raises(ValueError, match="sensitive"):
        store.publish("project-alpha", (record(content="token=abcdefghi"),))
    with pytest.raises(ValueError, match="provenance"):
        record(provenance=MemoryProvenance(None, None, None, None, None))
    with pytest.raises(ValueError, match="kind"):
        replace(record(), kind="UNKNOWN")  # type: ignore[arg-type]
    assert not list(runtime_root(tmp_path).glob("pm-*"))


def test_replacement_and_explicit_deletion_create_tombstone_versions(tmp_path: Path) -> None:
    """Replacement or deletion can never silently rewrite a prior source version."""

    store = ProjectMemoryStore(runtime_root(tmp_path))
    original = store.publish("project-alpha", (record(),))
    replacement = store.publish(
        "project-alpha",
        (record("memory-2", supersedes="memory-1"),),
        parent_memory_version=original.manifest.memory_version,
    )
    deleted = store.delete(
        "project-alpha", "memory-2", replacement.manifest.memory_version
    )

    assert replacement.manifest.memory_version != original.manifest.memory_version
    assert "memory-1" in replacement.tombstone_ids
    assert "memory-2" in deleted.tombstone_ids
    assert store.load("project-alpha", original.manifest.memory_version) == original


def test_child_source_rejects_a_supersession_not_in_its_parent(tmp_path: Path) -> None:
    """A child cannot claim replacement history that its verified parent lacks."""

    store = ProjectMemoryStore(runtime_root(tmp_path))
    parent = store.publish("project-alpha", (record(),))

    with pytest.raises(ProjectMemoryIntegrityError, match="supersedes"):
        store.publish(
            "project-alpha",
            (record("memory-2", supersedes="not-in-parent"),),
            parent_memory_version=parent.manifest.memory_version,
        )


def test_cache_rebuilds_from_verified_source_and_cannot_change_records(
    tmp_path: Path,
) -> None:
    """SQLite only supplies candidates; verified JSON remains authoritative."""

    store = ProjectMemoryStore(runtime_root(tmp_path))
    snapshot = store.publish("project-alpha", (record(),))
    index = ProjectMemoryIndex(store)
    cache_path = index.rebuild("project-alpha", snapshot.manifest.memory_version)
    cache_path.write_bytes(b"not sqlite")

    records = index.search(
        "project-alpha", snapshot.manifest.memory_version, "Docker user"
    )

    assert records == snapshot.records
    assert cache_path.read_bytes().startswith(b"SQLite format 3")


def test_context_selection_is_bounded_stable_and_explains_omissions(
    tmp_path: Path,
) -> None:
    """Selection must not cross projects or exceed declared context budgets."""

    store = ProjectMemoryStore(runtime_root(tmp_path))
    snapshot = store.publish(
        "project-alpha",
        (
            record("memory-run", content="Docker rule requires a non-root user."),
            record(
                "memory-other",
                content="Docker rule requires a non-root user.",
                provenance=MemoryProvenance(
                    "run-2", "event-2", "evidence-2", "RULE-DOCKER-002", None
                ),
            ),
            record("memory-large", content="Docker " + "x" * 80),
        ),
    )
    assembler = MemoryContextAssembler(ProjectMemoryIndex(store))
    context = assembler.select(
        project_id="project-alpha",
        memory_version=snapshot.manifest.memory_version,
        query="docker rule",
        active_run_references=("run-1", "RULE-DOCKER-001"),
        budget=MemoryQueryBudget(top_k=2, max_characters=60, max_tokens=10),
    )

    assert [item.memory_id for item in context.selected] == ["memory-run"]
    assert context.character_count <= 60
    assert context.token_count <= 10
    assert any(
        item.memory_id == "memory-other" and item.exclusion_reason == "duplicate_content"
        for item in context.omitted
    )
    assert {item.memory_id for item in context.omitted} >= {"memory-other", "memory-large"}


def test_context_selection_preserves_generator_active_run_references(
    tmp_path: Path,
) -> None:
    """Converting references for validation must not discard their ranking value."""

    store = ProjectMemoryStore(runtime_root(tmp_path))
    snapshot = store.publish(
        "project-alpha",
        (
            record(
                "memory-a",
                content="Same relevance text.",
                provenance=MemoryProvenance("run-other", None, None, "RULE-2", None),
            ),
            record(
                "memory-b",
                content="Same relevance text.",
                provenance=MemoryProvenance("run-active", None, None, "RULE-1", None),
            ),
        ),
    )

    context = MemoryContextAssembler(ProjectMemoryIndex(store)).select(
        project_id="project-alpha",
        memory_version=snapshot.manifest.memory_version,
        query="same",
        active_run_references=(item for item in ("run-active",)),
        budget=MemoryQueryBudget(top_k=1, max_characters=100, max_tokens=20),
    )

    assert [item.memory_id for item in context.selected] == ["memory-b"]


def test_store_rejects_non_runtime_and_symlink_escape_paths(tmp_path: Path) -> None:
    """A configured source root cannot resolve outside the repository runtime root."""

    with pytest.raises(ProjectMemoryIntegrityError, match="runtime root"):
        ProjectMemoryStore(Path(r"C:\ReleaseGuard\memory"))

    escaped = runtime_root(tmp_path) / "escape"
    try:
        escaped.symlink_to(Path.cwd(), target_is_directory=True)
    except OSError:
        pytest.skip("Windows account cannot create a symlink or junction")
    with pytest.raises(ProjectMemoryIntegrityError, match="runtime root"):
        ProjectMemoryStore(escaped)


def test_containment_rejects_resolved_external_paths_for_reads_and_writes(
    tmp_path: Path,
) -> None:
    """Portable coverage verifies both paths after resolution, without symlink privileges."""

    allowed = runtime_root(tmp_path)
    with pytest.raises(ProjectMemoryIntegrityError, match="escapes"):
        project_memory_module._validate_write_path(Path.cwd(), allowed, "artifact")
    with pytest.raises(ProjectMemoryIntegrityError, match="escapes"):
        project_memory_module._resolve_contained_path(Path.cwd(), allowed, "artifact")


def test_source_rejects_raw_conversation_and_lifecycle_ineligible_records(
    tmp_path: Path,
) -> None:
    """Conversation-shaped content is never serialized and inactive memory stays out of context."""

    with pytest.raises(ValueError, match="conversation"):
        record(content='{"messages":[{"role":"user","content":"release"}]}')
    store = ProjectMemoryStore(runtime_root(tmp_path))
    snapshot = store.publish(
        "project-alpha",
        (
            record("disabled", status=MemoryStatus.DISABLED),
            record("superseded", status=MemoryStatus.SUPERSEDED),
            replace(record("expired"), expires_at_utc="2000-01-01T00:00:00+00:00"),
            record(
                "human",
                kind=MemoryKind.HUMAN_CORRECTION,
                provenance=MemoryProvenance(None, None, None, None, "correction-1"),
            ),
        ),
    )

    context = MemoryContextAssembler(ProjectMemoryIndex(store)).select(
        project_id="project-alpha",
        memory_version=snapshot.manifest.memory_version,
        query="docker",
        active_run_references=(),
        budget=MemoryQueryBudget(top_k=5, max_characters=500, max_tokens=100),
    )

    assert [item.memory_id for item in context.selected] == ["human"]
    assert {item.exclusion_reason for item in context.omitted} >= {
        "disabled",
        "superseded",
        "expired",
    }


def test_cache_tamper_rebuilds_before_a_source_record_is_returned(tmp_path: Path) -> None:
    """A changed cache content cell cannot influence the record returned to callers."""

    store = ProjectMemoryStore(runtime_root(tmp_path))
    snapshot = store.publish("project-alpha", (record(),))
    index = ProjectMemoryIndex(store)
    cache_path = index.rebuild("project-alpha", snapshot.manifest.memory_version)
    connection = sqlite3.connect(cache_path)
    connection.execute("UPDATE memory_candidates SET content = 'forged content'")
    connection.commit()
    connection.close()

    returned = index.search(
        "project-alpha", snapshot.manifest.memory_version, "docker"
    )

    assert returned == snapshot.records


def test_store_does_not_overwrite_an_existing_verified_source(tmp_path: Path) -> None:
    """Republishing an identical source reads the immutable version rather than rewriting it."""

    store = ProjectMemoryStore(runtime_root(tmp_path))
    snapshot = store.publish("project-alpha", (record(),))
    source = runtime_root(tmp_path) / snapshot.manifest.memory_version / "records.json"
    original = source.read_bytes()

    repeated = store.publish("project-alpha", (record(),))

    assert repeated == snapshot
    assert source.read_bytes() == original
