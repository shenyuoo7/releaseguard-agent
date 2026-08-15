import hashlib
from pathlib import Path

import pytest

from releaseguard_agent.agent_tools import (
    ArtifactContextRequest,
    EvidenceSearchTool,
    FixPlanTool,
    RiskAnalysisTool,
    ScanProjectTool,
)
from releaseguard_agent.models.project_memory import (
    MemoryKind,
    MemoryProvenance,
    MemoryQueryBudget,
    MemoryStatus,
    ProjectMemoryRecord,
)
from releaseguard_agent.models.relation_index import RelationQueryBudget
from releaseguard_agent.rag import RuleRetrievalService, get_default_rule_index_path
from releaseguard_agent.rag.project_memory import ProjectMemoryStore
from releaseguard_agent.rag.relation_index import RelationIndexBuilder
from releaseguard_agent.services import ReleaseReviewService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_scan_and_evidence_tools_call_real_services() -> None:
    review = ScanProjectTool(ReleaseReviewService()).invoke(
        PROJECT_ROOT / "sample_projects" / "fastapi_bad_project",
        include_pytest_execution=False,
    )
    evidence = EvidenceSearchTool(
        RuleRetrievalService(get_default_rule_index_path())
    ).invoke("FastAPI dependency", mode="bm25", top_k=3)

    assert review.release_allowed is False
    assert len(evidence.evidence) == 3
    assert all(item.evidence_id for item in evidence.evidence)


def test_deterministic_risk_and_fix_tools_preserve_blocking_decision() -> None:
    review = ReleaseReviewService().review(
        project_path=PROJECT_ROOT / "sample_projects" / "fastapi_bad_project",
        include_pytest_execution=False,
    )

    risk = RiskAnalysisTool().invoke(review, review.retrieval_evidence)
    plan = FixPlanTool().invoke(review, risk.payload)

    assert risk.llm_attempted is False
    assert risk.payload["release_allowed"] is False
    assert risk.payload["analysis_source"] == "deterministic"
    assert plan
    assert all(step["validation"] for step in plan)


def test_evidence_tool_resolves_verified_artifacts_without_mutating_sources(
    tmp_path: Path,
) -> None:
    """A runtime read must not build, cache, or rewrite either immutable source."""

    root = PROJECT_ROOT / ".runtime" / "task4-agent-tools" / hashlib.sha256(
        str(tmp_path).encode("utf-8")
    ).hexdigest()[:16]
    relation_root = root / "relations"
    memory_root = root / "memory"
    relation = RelationIndexBuilder().build(
        get_default_rule_index_path(), relation_root
    )
    memory = ProjectMemoryStore(memory_root).publish(
        "project-alpha",
        (
            _memory_record(
                "memory-selected",
                "FastAPI release reviews require dependency evidence.",
            ),
            _memory_record(
                "memory-disabled",
                "This disabled content must not be selected.",
                status=MemoryStatus.DISABLED,
            ),
            _memory_record(
                "memory-expired",
                "This expired content must not be selected.",
                expires_at_utc="2020-01-01T00:00:00+00:00",
            ),
        ),
    )
    tool = EvidenceSearchTool(
        RuleRetrievalService(
            get_default_rule_index_path(),
            relation_snapshot_root=relation_root,
        ),
        relation_snapshot_root=relation_root,
        memory_root=memory_root,
    )
    before = _source_digests(root)

    resolved = tool.resolve_artifact_context(
        ArtifactContextRequest(
            project_id="project-alpha",
            query="FastAPI dependency evidence",
            active_run_references=("RG-DEPS-001",),
            retrieval_mode="graph_hybrid",
            relation_index_version=relation.manifest.index_version,
            memory_version=memory.manifest.memory_version,
            relation_budget=RelationQueryBudget(2, 24, 24, 8_000),
            memory_budget=MemoryQueryBudget(1, 200, 30),
            memory_as_of_utc="2026-08-15T00:00:00+00:00",
        )
    )

    assert resolved.trace.relation_index_version == relation.manifest.index_version
    assert resolved.trace.relation_sha256 == relation.snapshot_sha256
    assert resolved.trace.memory_version == memory.manifest.memory_version
    assert resolved.trace.memory_sha256 == memory.manifest.content_sha256
    assert resolved.trace.selected_memory_ids == ("memory-selected",)
    assert ("memory-disabled", "disabled") in resolved.trace.omitted_memory
    assert ("memory-expired", "expired") in resolved.trace.omitted_memory
    assert resolved.memory_context is not None
    assert resolved.memory_context.content.startswith("FastAPI release")
    assert resolved.relation_snapshot is not None
    assert _source_digests(root) == before
    assert not list(memory_root.rglob("*.sqlite3"))
    assert "FastAPI release reviews" not in repr(resolved.trace.to_dict())
    assert "FastAPI release reviews" not in repr(resolved)

    historical = tool.resolve_artifact_context(
        ArtifactContextRequest(
            project_id="project-alpha",
            query="expired content",
            active_run_references=(),
            retrieval_mode="graph_hybrid",
            relation_index_version=relation.manifest.index_version,
            memory_version=memory.manifest.memory_version,
            relation_budget=RelationQueryBudget(2, 24, 24, 8_000),
            memory_budget=MemoryQueryBudget(1, 200, 30),
            memory_as_of_utc="2019-01-01T00:00:00+00:00",
        )
    )
    assert historical.trace.selected_memory_ids == ("memory-expired",)

    scoped_out = tool.resolve_artifact_context(
        ArtifactContextRequest(
            project_id="project-beta",
            query="FastAPI dependency evidence",
            active_run_references=(),
            retrieval_mode="graph_hybrid",
            relation_index_version=relation.manifest.index_version,
            memory_version=memory.manifest.memory_version,
            relation_budget=RelationQueryBudget(2, 24, 24, 8_000),
            memory_budget=MemoryQueryBudget(1, 200, 30),
            memory_as_of_utc="2026-08-15T00:00:00+00:00",
        )
    )
    assert scoped_out.memory_context is None
    assert scoped_out.trace.selected_memory_ids == ()
    assert (
        scoped_out.trace.memory_fallback_reason
        == "memory_project_scope_mismatch"
    )

    exhausted = tool.resolve_artifact_context(
        ArtifactContextRequest(
            project_id="project-alpha",
            query="FastAPI dependency evidence",
            active_run_references=(),
            retrieval_mode="graph_hybrid",
            relation_index_version=relation.manifest.index_version,
            memory_version=memory.manifest.memory_version,
            relation_budget=RelationQueryBudget(2, 1, 1, 1),
            memory_budget=MemoryQueryBudget(1, 1, 1),
            memory_as_of_utc="2026-08-15T00:00:00+00:00",
        )
    )
    assert exhausted.trace.relation_budget == (
        ("max_context_characters", 1),
        ("max_edges", 1),
        ("max_hops", 2),
        ("max_nodes", 1),
    )
    assert exhausted.trace.memory_mode == "evidence_gap"
    assert exhausted.trace.memory_fallback_reason == "memory_budget_exhausted"
    assert ("memory-selected", "character_budget") in exhausted.trace.omitted_memory

    with pytest.raises(ValueError, match="sensitive"):
        _memory_record(
            "memory-secret",
            "api_key=sk-task4-sensitive-memory-value",
        )

    version_root = relation_root / relation.manifest.index_version
    hidden_root = relation_root / f"hidden-{relation.manifest.index_version}"
    version_root.rename(hidden_root)
    missing_preflight = tool.resolve_artifact_context(
        ArtifactContextRequest(
            project_id=None,
            query="RG-DEPS-001",
            active_run_references=("RG-DEPS-001",),
            retrieval_mode="local_graph",
            relation_index_version=relation.manifest.index_version,
            memory_version=None,
            relation_budget=RelationQueryBudget(2, 24, 24, 8_000),
            memory_budget=MemoryQueryBudget(1, 200, 30),
            memory_as_of_utc="2026-08-15T00:00:00+00:00",
        )
    )
    hidden_root.rename(version_root)
    assert (
        missing_preflight.trace.relation_fallback_reason
        == "relation_snapshot_missing"
    )
    still_missing = tool.invoke(
        "RG-DEPS-001",
        mode="local_graph",
        top_k=3,
        seed_rule_ids=("RG-DEPS-001",),
        relation_index_version=relation.manifest.index_version,
        relation_budget=RelationQueryBudget(2, 24, 24, 8_000),
        relation_snapshot=None,
        relation_fallback_reason=(
            missing_preflight.trace.relation_fallback_reason
        ),
    )
    assert still_missing.degraded_reason == "relation_snapshot_missing"

    manifest = relation_root / relation.manifest.index_version / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    pinned = tool.invoke(
        "RG-DEPS-001",
        mode="local_graph",
        top_k=3,
        seed_rule_ids=("RG-DEPS-001",),
        relation_index_version=relation.manifest.index_version,
        relation_budget=RelationQueryBudget(2, 24, 24, 8_000),
        relation_snapshot=resolved.relation_snapshot,
    )
    assert pinned.degraded_reason != "relation_snapshot_invalid"


def _memory_record(
    memory_id: str,
    content: str,
    *,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    expires_at_utc: str | None = None,
) -> ProjectMemoryRecord:
    return ProjectMemoryRecord(
        memory_id=memory_id,
        project_id="project-alpha",
        kind=MemoryKind.PROJECT_FACT,
        content=content,
        provenance=MemoryProvenance(
            run_id="run-task4",
            event_id="event-task4",
            evidence_id=None,
            rule_id="RG-DEPS-001",
            human_correction_id=None,
        ),
        created_at_utc="2026-08-15T00:00:00+00:00",
        updated_at_utc="2026-08-15T00:00:00+00:00",
        status=status,
        confidence=0.9,
        supersedes=None,
        expires_at_utc=expires_at_utc,
        memory_version="pm-pending",
    )


def _source_digests(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
