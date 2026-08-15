import json
import shutil
import tempfile
from pathlib import Path

import pytest

from releaseguard_agent.agent_tools import EvidenceSearchTool
from releaseguard_agent.models.relation_index import RelationQueryBudget
from releaseguard_agent.rag import RuleRetrievalService
from releaseguard_agent.rag.relation_index import RelationIndexBuilder


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = PROJECT_ROOT / ".runtime" / "relation-retrieval-tests"
RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "relation_retrieval"
FIXTURE_INDEX_PATH = Path("tests/fixtures/relation_retrieval/rule_index.md")
FIXTURE_INDEX_VERSION = "ri-a07a5a45823c0f01d8008a205e75db1066dea90a3ee7bc9d037103ab58a300de"

HEADER = (
    "| rule_id | rule_name | checker | source | support_level | "
    "priority | blocking_policy | evidence_type | phase |"
)
SEPARATOR = "|---|---|---|---|---|---|---|---|---|"
SOURCE_HEADER = (
    "| rule_id | ReleaseGuard rule | support_level | blocking_policy | "
    "evidence_type | boundary |"
)
SOURCE_SEPARATOR = "|---|---|---|---|---|---|"


def _build_service(root: Path) -> tuple[RuleRetrievalService, str, Path]:
    corpus_root = root / "corpus"
    corpus_root.mkdir()
    index_path = corpus_root / "rule_index.md"
    index_path.write_text(
        "\n".join(
            (
                HEADER,
                SEPARATOR,
                "| RG-TEST-001 | Tests exist | TestChecker | trusted | "
                "source-backed | high | block | directory_exists | phase-1 |",
                "| RG-TEST-002 | Health exists | ApiChecker | trusted | "
                "source-backed | medium | warn | endpoint_exists | phase-2 |",
                "",
            )
        ),
        encoding="utf-8",
    )
    sources = corpus_root / "sources"
    sources.mkdir()
    (sources / "trusted.md").write_text(
        "\n".join(
            (
                "# Trusted",
                "",
                "## Source",
                "",
                "- URL: https://example.com/trusted",
                "- Type: documentation",
                "",
                "## ReleaseGuard Rule Mapping",
                "",
                SOURCE_HEADER,
                SOURCE_SEPARATOR,
                "| RG-TEST-001 | Verify tests | source-backed | block | "
                "directory_exists | Trusted tests boundary. |",
                "| RG-TEST-002 | Verify health | source-backed | warn | "
                "endpoint_exists | Trusted health boundary. |",
                "",
            )
        ),
        encoding="utf-8",
    )
    snapshot_root = root / "snapshots"
    snapshot = RelationIndexBuilder().build(index_path, snapshot_root)
    return (
        RuleRetrievalService(index_path, relation_snapshot_root=snapshot_root),
        snapshot.manifest.index_version,
        snapshot_root,
    )


def _budget(**overrides: int) -> RelationQueryBudget:
    values = {
        "max_hops": 2,
        "max_nodes": 16,
        "max_edges": 16,
        "max_context_characters": 4_000,
    }
    values.update(overrides)
    return RelationQueryBudget(**values)


def _fixed_snapshot_service(root: Path) -> RuleRetrievalService:
    """Load Task 1-compatible fixture bytes rather than building a graph in-test."""
    snapshot_root = root / "snapshots"
    shutil.copytree(FIXTURE_ROOT / "snapshot", snapshot_root)
    for artifact in snapshot_root.rglob("*.json"):
        artifact.write_bytes(artifact.read_bytes().rstrip(b"\r\n"))
    return RuleRetrievalService(
        FIXTURE_INDEX_PATH,
        relation_snapshot_root=snapshot_root,
    )


def _build_large_service(
    root: Path, *, rule_count: int
) -> tuple[RuleRetrievalService, str]:
    corpus_root = root / "large-corpus"
    corpus_root.mkdir()
    rows = [HEADER, SEPARATOR]
    mappings = [
        "# Large Fixture",
        "",
        "## Source",
        "",
        "- URL: https://example.com/large-fixture",
        "- Type: documentation",
        "",
        "## ReleaseGuard Rule Mapping",
        "",
        SOURCE_HEADER,
        SOURCE_SEPARATOR,
    ]
    for number in range(1, rule_count + 1):
        rule_id = f"RG-CAP-{number:03d}"
        rows.append(
            f"| {rule_id} | Candidate {number:03d} | CapChecker | fixture | "
            "source-backed | medium | warn | candidate_exists | cap-phase |"
        )
        mappings.append(
            f"| {rule_id} | Verify candidate {number:03d} | source-backed | warn | "
            "candidate_exists | Fixed large fixture boundary. |"
        )
    index_path = corpus_root / "rule_index.md"
    index_path.write_text("\n".join((*rows, "")), encoding="utf-8")
    source_directory = corpus_root / "sources"
    source_directory.mkdir()
    (source_directory / "trusted.md").write_text(
        "\n".join((*mappings, "")), encoding="utf-8"
    )
    snapshot_root = root / "large-snapshots"
    snapshot = RelationIndexBuilder().build(index_path, snapshot_root)
    return (
        RuleRetrievalService(index_path, relation_snapshot_root=snapshot_root),
        snapshot.manifest.index_version,
    )


def _build_multi_source_service(
    root: Path, *, source_count: int
) -> tuple[RuleRetrievalService, str]:
    corpus_root = root / "multi-source-corpus"
    corpus_root.mkdir()
    index_path = corpus_root / "rule_index.md"
    index_path.write_text(
        "\n".join(
            (
                HEADER,
                SEPARATOR,
                "| RG-POOL-001 | Pool rule | PoolChecker | pool source | "
                "source-backed | high | block | pool_exists | pool-phase |",
                "",
            )
        ),
        encoding="utf-8",
    )
    source_directory = corpus_root / "sources"
    source_directory.mkdir()
    for number in range(1, source_count + 1):
        boundary = "Signal " * 100 if number == 6 else "Other pool boundary."
        (source_directory / f"source-{number:02d}.md").write_text(
            "\n".join(
                (
                    f"# Pool Source {number:02d}",
                    "",
                    "## Source",
                    "",
                    f"- URL: https://example.com/pool-{number:02d}",
                    "- Type: documentation",
                    "",
                    "## ReleaseGuard Rule Mapping",
                    "",
                    SOURCE_HEADER,
                    SOURCE_SEPARATOR,
                    "| RG-POOL-001 | Pool candidate | source-backed | block | "
                    f"pool_exists | {boundary}|",
                    "",
                )
            ),
            encoding="utf-8",
        )
    snapshot_root = root / "multi-source-snapshots"
    snapshot = RelationIndexBuilder().build(index_path, snapshot_root)
    return (
        RuleRetrievalService(index_path, relation_snapshot_root=snapshot_root),
        snapshot.manifest.index_version,
    )


def test_fixed_task1_snapshot_fixture_is_usable_for_relation_retrieval() -> None:
    """Would fail if a checked-in immutable Task 1 snapshot became incompatible."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        service = _fixed_snapshot_service(Path(temporary_directory))
        result = service.retrieve(
            "fixed fixture",
            mode="local_graph",
            top_k=2,
            seed_rule_ids=("RG-FIXTURE-001",),
            relation_index_version=FIXTURE_INDEX_VERSION,
            relation_budget=_budget(),
        )

    assert result.mode_used == "local_graph"
    assert result.evidence[0].index_version == FIXTURE_INDEX_VERSION


def test_stale_snapshot_falls_back_before_mixing_current_corpus_evidence() -> None:
    """Would fail if snapshot graph IDs were joined to changed corpus metadata."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        root = Path(temporary_directory)
        _, version, snapshot_root = _build_service(root)
        index_path = root / "corpus" / "rule_index.md"
        index_path.write_text(
            index_path.read_text(encoding="utf-8").replace(
                "Tests exist", "Changed tests exist"
            ),
            encoding="utf-8",
        )
        service = RuleRetrievalService(
            index_path, relation_snapshot_root=snapshot_root
        )
        result = service.retrieve(
            "tests",
            mode="local_graph",
            top_k=2,
            seed_rule_ids=("RG-TEST-001",),
            relation_index_version=version,
            relation_budget=_budget(),
        )

    assert result.mode_used == "bm25"
    assert result.degraded_reason == "relation_source_index_mismatch"
    assert all(not item.relation_paths for item in result.evidence)


def test_incompatible_snapshot_schema_falls_back_with_precise_reason() -> None:
    """Would fail if an unsupported Task 1 schema were treated as generic graph data."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        service, version, snapshot_root = _build_service(Path(temporary_directory))
        manifest_path = snapshot_root / version / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema_version"] = "999"
        manifest_path.write_bytes(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        result = service.retrieve(
            "tests",
            mode="local_graph",
            top_k=2,
            seed_rule_ids=("RG-TEST-001",),
            relation_index_version=version,
            relation_budget=_budget(),
        )

    assert result.mode_used == "bm25"
    assert result.degraded_reason == "relation_snapshot_incompatible"


def test_graph_hybrid_pins_rrf_order_channel_cap_and_final_cap() -> None:
    """Would fail if RRF fusion changed its documented order or candidate bounds."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        service, version = _build_large_service(
            Path(temporary_directory), rule_count=25
        )
        arguments = {
            "mode": "graph_hybrid",
            "seed_rule_ids": ("RG-CAP-025",),
            "relation_index_version": version,
            "relation_budget": _budget(max_nodes=100, max_edges=100),
        }
        unbounded = service.retrieve("fixture", top_k=25, **arguments)
        capped = service.retrieve("fixture", top_k=5, **arguments)

    assert len(unbounded.evidence) == 21
    assert [item.chunk_id for item in capped.evidence] == [
        "RG-CAP-001:chunk-01",
        "RG-CAP-025:chunk-01",
        "RG-CAP-002:chunk-01",
        "RG-CAP-003:chunk-01",
        "RG-CAP-004:chunk-01",
    ]


def test_graph_hybrid_collects_fixed_channel_pools_before_final_truncation() -> None:
    """Would fail if caller top_k limited either channel before RRF fusion."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        service, version = _build_multi_source_service(
            Path(temporary_directory), source_count=25
        )
        arguments = {
            "mode": "graph_hybrid",
            "seed_rule_ids": ("RG-POOL-001",),
            "relation_index_version": version,
            "relation_budget": _budget(max_nodes=100, max_edges=100),
        }
        full = service.retrieve("signal", top_k=25, **arguments)
        final = service.retrieve("signal", top_k=5, **arguments)

    assert len(full.evidence) == 20
    assert {item.metadata["text_rank"] for item in full.evidence} == {
        str(rank) for rank in range(1, 21)
    }
    assert {item.metadata["graph_rank"] for item in full.evidence} == {
        str(rank) for rank in range(1, 21)
    }
    assert [item.chunk_id for item in final.evidence] == [
        "RG-POOL-001:chunk-01",
        "RG-POOL-001:chunk-02",
        "RG-POOL-001:chunk-06",
        "RG-POOL-001:chunk-03",
        "RG-POOL-001:chunk-04",
    ]
    signal_item = final.evidence[2]
    assert signal_item.fusion_score == pytest.approx(1 / 61 + 1 / 66)
    assert signal_item.rerank_score == pytest.approx(1 / 61 + 1 / 66)
    assert final.evidence == full.evidence[:5]


def test_local_graph_returns_only_verified_chunk_paths_with_provenance() -> None:
    """Would fail if graph retrieval invents a path or loses source provenance."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        service, version, _ = _build_service(Path(temporary_directory))

        result = service.retrieve(
            "unrelated query must not seed facts",
            mode="local_graph",
            top_k=3,
            seed_rule_ids=("RG-TEST-001",),
            relation_index_version=version,
            relation_budget=_budget(),
        )

    assert result.mode_used == "local_graph"
    assert result.degraded_reason is None
    assert result.evidence
    assert {item.rule_id for item in result.evidence} == {"RG-TEST-001"}
    for item in result.evidence:
        assert item.index_version == version
        assert item.relation_paths
        assert item.source_url == "https://example.com/trusted"
        assert all(path.index_version == version for path in item.relation_paths)
        assert all(path.hop_count in {1, 2} for path in item.relation_paths)
        assert all(path.node_ids[0] == "rule:RG-TEST-001" for path in item.relation_paths)
        assert all(item.chunk_id in path.source_chunk_ids for path in item.relation_paths)


def test_graph_hybrid_fuses_text_and_graph_candidates_stably() -> None:
    """Would fail if graph/text fusion ordering depends on incidental iteration order."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        service, version, _ = _build_service(Path(temporary_directory))
        arguments = {
            "mode": "graph_hybrid",
            "top_k": 2,
            "seed_rule_ids": ("RG-TEST-001",),
            "relation_index_version": version,
            "relation_budget": _budget(),
        }
        first = service.retrieve("health tests", **arguments)
        second = service.retrieve("health tests", **arguments)

    assert first.to_dict() == second.to_dict()
    assert first.mode_used == "graph_hybrid"
    seeded = next(item for item in first.evidence if item.rule_id == "RG-TEST-001")
    assert "local_graph" in seeded.retrieval_method
    assert seeded.relation_paths
    assert seeded.metadata["graph_path_score"]
    assert seeded.metadata["text_score"]
    assert seeded.fusion_score > 0
    assert seeded.rerank_score >= seeded.fusion_score


@pytest.mark.parametrize(
    ("seed_rule_ids", "relation_index_version", "budget", "reason"),
    (
        ((), "ri-" + "0" * 64, _budget(), "relation_seed_required"),
        (("RG-TEST-001",), "ri-" + "0" * 64, _budget(), "relation_snapshot_missing"),
        (("RG-TEST-001",), None, _budget(max_edges=1), "relation_edge_budget_exceeded"),
        (("RG-TEST-001",), None, _budget(max_nodes=1), "relation_node_budget_exceeded"),
        (("RG-TEST-001",), None, _budget(max_context_characters=1), "relation_context_budget_exceeded"),
        (("RG-TEST-001",), None, _budget(max_hops=3), "relation_hop_budget_exceeded"),
    ),
)
def test_relation_failures_fall_back_to_deterministic_text(
    seed_rule_ids: tuple[str, ...],
    relation_index_version: str | None,
    budget: RelationQueryBudget,
    reason: str,
) -> None:
    """Would fail if a failed graph read or budget emits invented graph evidence."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        service, version, _ = _build_service(Path(temporary_directory))
        result = service.retrieve(
            "tests health",
            mode="local_graph",
            top_k=2,
            seed_rule_ids=seed_rule_ids,
            relation_index_version=relation_index_version or version,
            relation_budget=budget,
        )

    assert result.mode_used == "bm25"
    assert result.degraded_reason == reason
    assert all(not item.relation_paths for item in result.evidence)
    assert all(item.index_version is None for item in result.evidence)


def test_corrupt_snapshot_falls_back_without_graph_paths() -> None:
    """Would fail if a corrupt immutable snapshot were trusted by retrieval."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        service, version, snapshot_root = _build_service(Path(temporary_directory))
        nodes_path = snapshot_root / version / "nodes.json"
        payload = json.loads(nodes_path.read_text(encoding="utf-8"))
        payload[0]["description"] = "tampered"
        nodes_path.write_text(json.dumps(payload), encoding="utf-8")
        result = service.retrieve(
            "tests",
            mode="graph_hybrid",
            top_k=2,
            seed_rule_ids=("RG-TEST-001",),
            relation_index_version=version,
            relation_budget=_budget(),
        )

    assert result.mode_used == "bm25"
    assert result.degraded_reason == "relation_snapshot_invalid"
    assert all(not item.relation_paths for item in result.evidence)


def test_legacy_modes_keep_empty_relation_fields() -> None:
    """Would fail if graph support changed legacy evidence contracts."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        service, _, _ = _build_service(Path(temporary_directory))
        for mode in ("exact", "bm25", "vector", "hybrid"):
            result = service.retrieve("RG-TEST-001", mode=mode, top_k=2)
            assert all(item.relation_paths == () for item in result.evidence)
            assert all(item.index_version is None for item in result.evidence)


def test_evidence_tool_validates_relation_inputs_and_traces_only_ids() -> None:
    """Would fail if the public tool accepts unsafe inputs or traces source text."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        service, version, _ = _build_service(Path(temporary_directory))
        tool = EvidenceSearchTool(service)
        with pytest.raises(ValueError, match="relation_index_version"):
            tool.invoke(
                "tests",
                mode="local_graph",
                top_k=1,
                relation_index_version="not-a-version",
            )
        with pytest.raises(ValueError, match="relation_budget"):
            tool.invoke(
                "tests",
                mode="local_graph",
                top_k=1,
                relation_budget="bad",  # type: ignore[arg-type]
            )
        from releaseguard_agent.observability import ExecutionTracer

        tracer = ExecutionTracer(run_id="relation-trace")
        tool.invoke(
            "tests",
            mode="local_graph",
            top_k=1,
            seed_rule_ids=("RG-TEST-001",),
            relation_index_version=version,
            relation_budget=_budget(),
            tracer=tracer,
        )

    event = tracer.to_dict()["events"][0]
    assert event["relation_index_version"] == version
    assert event["relation_path_ids"]
    assert "Trusted tests boundary" not in repr(event)
