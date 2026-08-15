import json
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
