import json
import os
import subprocess
import tempfile
from hashlib import sha256
from pathlib import Path

import pytest

from releaseguard_agent.models.relation_index import (
    RelationEdgeType,
    RelationNodeType,
    RelationQueryBudget,
)
from releaseguard_agent.rag.relation_index import (
    RelationIndexBuilder,
    RelationIndexIntegrityError,
    RelationIndexStore,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = PROJECT_ROOT / ".runtime" / "relation-index-tests"
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


def _write_corpus(root: Path, *, include_second_rule: bool = True) -> Path:
    index_path = root / "rule_index.md"
    rows = [
        "| RG-TEST-001 | Tests directory exists | TestChecker | "
        "pytest documentation | source-backed | high | block | "
        "directory_exists | phase-1 |",
    ]
    if include_second_rule:
        rows.append(
            "| RG-TEST-002 | Health endpoint exists | ApiChecker | "
            "FastAPI documentation | source-backed | medium | warn | "
            "endpoint_exists | phase-2 |"
        )
    index_path.write_text(
        "\n".join((HEADER, SEPARATOR, *rows)) + "\n", encoding="utf-8"
    )
    source_directory = root / "sources"
    source_directory.mkdir(exist_ok=True)
    (source_directory / "trusted.md").write_text(
        "\n".join(
            (
                "# Trusted Source",
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
                "| RG-TEST-001 | Verify test layout | source-backed | block | "
                "directory_exists | Trusted boundary one. |",
                "| RG-TEST-002 | Verify health endpoint | source-backed | warn | "
                "endpoint_exists | Trusted boundary two. |",
                "",
            )
        ),
        encoding="utf-8",
    )
    return index_path


def _build_fixture(root: Path):
    corpus_root = root / "corpus"
    corpus_root.mkdir()
    index_path = _write_corpus(corpus_root)
    output_root = root / "snapshots"
    return index_path, output_root


def _snapshot_path(output_root: Path, index_version: str, filename: str) -> Path:
    return output_root / index_version / filename


def _write_canonical_json(path: Path, value: object) -> bytes:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(raw)
    return raw


def _rewrite_artifact_digest(
    output_root: Path,
    index_version: str,
    *,
    artifact_name: str,
    raw: bytes,
) -> None:
    manifest_path = _snapshot_path(output_root, index_version, "manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[f"{artifact_name}_sha256"] = sha256(raw).hexdigest()
    _write_canonical_json(manifest_path, manifest)


def _rewrite_artifact_digests(
    output_root: Path,
    index_version: str,
    *,
    nodes_raw: bytes,
    edges_raw: bytes,
) -> None:
    manifest_path = _snapshot_path(output_root, index_version, "manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["nodes_sha256"] = sha256(nodes_raw).hexdigest()
    manifest["edges_sha256"] = sha256(edges_raw).hexdigest()
    _write_canonical_json(manifest_path, manifest)


def _link_directory(link_path: Path, target_path: Path) -> None:
    try:
        os.symlink(target_path, link_path, target_is_directory=True)
    except OSError:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_builds_trusted_relation_nodes_edges_and_chunk_provenance() -> None:
    """Would fail if trusted corpus records lose any required relation."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        index_path, output_root = _build_fixture(Path(temporary_directory))

        snapshot = RelationIndexBuilder().build(index_path, output_root)

        assert {node.node_type for node in snapshot.nodes} == {
            RelationNodeType.RULE,
            RelationNodeType.CHECKER,
            RelationNodeType.PHASE,
            RelationNodeType.SOURCE,
            RelationNodeType.CHUNK,
        }
        rule_node = next(
            node for node in snapshot.nodes if node.node_id == "rule:RG-TEST-001"
        )
        assert rule_node.canonical_name == "RG-TEST-001"
        assert rule_node.source_chunk_ids == ("RG-TEST-001:chunk-01",)
        assert {
            edge.relation_type for edge in snapshot.edges
        } == {
            RelationEdgeType.RULE_CHECKED_BY,
            RelationEdgeType.RULE_APPLIES_TO_PHASE,
            RelationEdgeType.RULE_SOURCED_BY,
            RelationEdgeType.RULE_HAS_CHUNK,
        }
        assert all(edge.source_chunk_ids for edge in snapshot.edges)


def test_repeated_identical_builds_have_stable_content_addressed_identity() -> None:
    """Would fail if ordering or volatile metadata changed an identical snapshot."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        index_path, output_root = _build_fixture(Path(temporary_directory))
        builder = RelationIndexBuilder()

        first = builder.build(index_path, output_root)
        second = builder.build(index_path, output_root)

        assert first.manifest.index_version == second.manifest.index_version
        assert first.snapshot_sha256 == second.snapshot_sha256
        assert first.nodes == second.nodes
        assert first.edges == second.edges


def test_identical_input_has_reproducible_identity_across_fresh_runtime_roots() -> None:
    """Would fail if wall-clock metadata changes a fresh snapshot identity."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        index_path, first_output_root = _build_fixture(Path(temporary_directory))
        second_output_root = Path(temporary_directory) / "second-snapshots"
        builder = RelationIndexBuilder()

        first = builder.build(index_path, first_output_root)
        second = builder.build(index_path, second_output_root)

        assert first.manifest.index_version == second.manifest.index_version
        assert first.snapshot_sha256 == second.snapshot_sha256


def test_store_rejects_coordinated_graph_and_manifest_digest_tampering() -> None:
    """Would fail if a validly hashed altered graph retains its original identity."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        index_path, output_root = _build_fixture(Path(temporary_directory))
        snapshot = RelationIndexBuilder().build(index_path, output_root)
        nodes_path = _snapshot_path(
            output_root, snapshot.manifest.index_version, "nodes.json"
        )
        edges_path = _snapshot_path(
            output_root, snapshot.manifest.index_version, "edges.json"
        )
        nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
        edges = json.loads(edges_path.read_text(encoding="utf-8"))
        nodes[0]["description"] = "Coordinated semantic mutation."
        edges[0]["source_chunk_ids"] = ["RG-TEST-002:chunk-01"]
        nodes_raw = _write_canonical_json(nodes_path, nodes)
        edges_raw = _write_canonical_json(edges_path, edges)
        _rewrite_artifact_digests(
            output_root,
            snapshot.manifest.index_version,
            nodes_raw=nodes_raw,
            edges_raw=edges_raw,
        )

        with pytest.raises(RelationIndexIntegrityError, match="content identity"):
            RelationIndexStore(output_root).load(snapshot.manifest.index_version)


def test_store_rederives_change_set_from_parent_and_current_graph() -> None:
    """Would fail if an altered child delta is trusted without recomputation."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        index_path, output_root = _build_fixture(Path(temporary_directory))
        builder = RelationIndexBuilder()
        parent = builder.build(index_path, output_root)
        _write_corpus(index_path.parent, include_second_rule=False)
        child = builder.build(index_path, output_root, parent_snapshot=parent)
        manifest_path = _snapshot_path(
            output_root, child.manifest.index_version, "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["change_set"]["tombstoned_node_ids"] = []
        _write_canonical_json(manifest_path, manifest)

        with pytest.raises(RelationIndexIntegrityError, match="change set"):
            RelationIndexStore(output_root).load(child.manifest.index_version)


def test_rejects_non_runtime_output_roots_and_recursive_parent_traversal() -> None:
    """Would fail if output or a recursive parent can escape the runtime root."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        index_path, output_root = _build_fixture(Path(temporary_directory))
        disallowed_root = PROJECT_ROOT / "outputs" / "relation-index"

        with pytest.raises(RelationIndexIntegrityError, match="runtime root"):
            RelationIndexStore(disallowed_root)
        with pytest.raises(RelationIndexIntegrityError, match="runtime root"):
            RelationIndexStore(Path("C:/releaseguard-relation-index"))
        with pytest.raises(RelationIndexIntegrityError, match="runtime root"):
            RelationIndexBuilder().build(
                index_path.parent / "missing-rule-index.md", disallowed_root
            )

        snapshot = RelationIndexBuilder().build(index_path, output_root)
        manifest_path = _snapshot_path(
            output_root, snapshot.manifest.index_version, "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["parent_index_version"] = "../escaped-parent"
        _write_canonical_json(manifest_path, manifest)

        with pytest.raises(
            RelationIndexIntegrityError, match="Invalid relation index version"
        ):
            RelationIndexStore(output_root).load(snapshot.manifest.index_version)


def test_store_rejects_top_level_and_parent_symlink_escapes() -> None:
    """Would fail if a valid version name resolves through a link outside runtime."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        index_path, output_root = _build_fixture(Path(temporary_directory))
        builder = RelationIndexBuilder()
        parent = builder.build(index_path, output_root)
        parent_path = output_root / parent.manifest.index_version
        parent_path.rename(output_root / "saved-parent")
        _link_directory(parent_path, PROJECT_ROOT)

        with pytest.raises(
            RelationIndexIntegrityError, match="outside the approved runtime root"
        ):
            RelationIndexStore(output_root).load(parent.manifest.index_version)

        os.rmdir(parent_path)
        (output_root / "saved-parent").rename(parent_path)
        _write_corpus(index_path.parent, include_second_rule=False)
        child = builder.build(index_path, output_root, parent_snapshot=parent)
        parent_path.rename(output_root / "saved-parent-again")
        _link_directory(parent_path, PROJECT_ROOT)

        with pytest.raises(
            RelationIndexIntegrityError, match="outside the approved runtime root"
        ):
            RelationIndexStore(output_root).load(child.manifest.index_version)


def test_suppresses_duplicate_relations_and_validates_query_budget() -> None:
    """Would fail if duplicate corpus links leak or invalid traversal caps pass."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        index_path, output_root = _build_fixture(Path(temporary_directory))

        snapshot = RelationIndexBuilder().build(index_path, output_root)

        edge_keys = {
            (edge.source_node_id, edge.target_node_id, edge.relation_type)
            for edge in snapshot.edges
        }
        assert len(edge_keys) == len(snapshot.edges)
        with pytest.raises(ValueError, match="max_hops"):
            RelationQueryBudget(0, 1, 1, 1)
        with pytest.raises(ValueError, match="max_nodes"):
            RelationQueryBudget(1, 0, 1, 1)
        with pytest.raises(ValueError, match="max_edges"):
            RelationQueryBudget(1, 1, 0, 1)
        with pytest.raises(ValueError, match="max_context_characters"):
            RelationQueryBudget(1, 1, 1, 0)


def test_source_deletion_creates_child_version_with_tombstones() -> None:
    """Would fail if source removals overwrite history or omit tombstones."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        index_path, output_root = _build_fixture(Path(temporary_directory))
        builder = RelationIndexBuilder()
        first = builder.build(index_path, output_root)
        _write_corpus(index_path.parent, include_second_rule=False)

        child = builder.build(index_path, output_root, parent_snapshot=first)

        assert child.manifest.parent_index_version == first.manifest.index_version
        assert child.manifest.index_version != first.manifest.index_version
        assert "rule:RG-TEST-002" in child.manifest.change_set.tombstoned_node_ids
        assert (output_root / first.manifest.index_version).is_dir()

        index_path.write_text(
            index_path.read_text(encoding="utf-8").replace(
                "Tests directory exists", "Tests layout exists"
            ),
            encoding="utf-8",
        )
        grandchild = builder.build(index_path, output_root, parent_snapshot=child)

        assert grandchild.manifest.parent_index_version == child.manifest.index_version
        assert "rule:RG-TEST-001" in grandchild.manifest.change_set.updated_node_ids


@pytest.mark.parametrize(
    ("filename", "mutate"),
    (
        ("nodes.json", lambda value: value.__setitem__(0, {"broken": True})),
        ("edges.json", lambda value: value.__setitem__(0, {"broken": True})),
    ),
)
def test_store_rejects_modified_content_addressed_artifact(
    filename: str,
    mutate: object,
) -> None:
    """Would fail if a changed payload bypasses its manifest digest."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        index_path, output_root = _build_fixture(Path(temporary_directory))
        snapshot = RelationIndexBuilder().build(index_path, output_root)
        artifact_path = _snapshot_path(
            output_root, snapshot.manifest.index_version, filename
        )
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert callable(mutate)
        mutate(payload)
        _write_canonical_json(artifact_path, payload)

        with pytest.raises(RelationIndexIntegrityError):
            RelationIndexStore(output_root).load(snapshot.manifest.index_version)


def test_store_rejects_manifest_mismatch_and_unknown_node_or_edge_types() -> None:
    """Would fail if malformed relation metadata enters a loaded snapshot."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        index_path, output_root = _build_fixture(Path(temporary_directory))
        snapshot = RelationIndexBuilder().build(index_path, output_root)
        manifest_path = _snapshot_path(
            output_root, snapshot.manifest.index_version, "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["nodes_sha256"] = "0" * 64
        _write_canonical_json(manifest_path, manifest)

        with pytest.raises(RelationIndexIntegrityError):
            RelationIndexStore(output_root).load(snapshot.manifest.index_version)

        _write_canonical_json(manifest_path, snapshot.manifest.to_dict())
        nodes_path = _snapshot_path(
            output_root, snapshot.manifest.index_version, "nodes.json"
        )
        nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
        nodes[0]["node_type"] = "UNKNOWN_NODE"
        raw_nodes = _write_canonical_json(nodes_path, nodes)
        _rewrite_artifact_digest(
            output_root,
            snapshot.manifest.index_version,
            artifact_name="nodes",
            raw=raw_nodes,
        )

        with pytest.raises(RelationIndexIntegrityError):
            RelationIndexStore(output_root).load(snapshot.manifest.index_version)

        _write_canonical_json(nodes_path, [node.to_dict() for node in snapshot.nodes])
        _rewrite_artifact_digest(
            output_root,
            snapshot.manifest.index_version,
            artifact_name="nodes",
            raw=nodes_path.read_bytes(),
        )
        edges_path = _snapshot_path(
            output_root, snapshot.manifest.index_version, "edges.json"
        )
        edges = json.loads(edges_path.read_text(encoding="utf-8"))
        edges[0]["relation_type"] = "UNKNOWN_EDGE"
        raw_edges = _write_canonical_json(edges_path, edges)
        _rewrite_artifact_digest(
            output_root,
            snapshot.manifest.index_version,
            artifact_name="edges",
            raw=raw_edges,
        )

        with pytest.raises(RelationIndexIntegrityError):
            RelationIndexStore(output_root).load(snapshot.manifest.index_version)


def test_store_rejects_dangling_edge_and_invalid_parent_without_mutating_verified_snapshot() -> None:
    """Would fail if bad snapshots load or damage a previously verified version."""
    with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as temporary_directory:
        index_path, output_root = _build_fixture(Path(temporary_directory))
        first = RelationIndexBuilder().build(index_path, output_root)
        store = RelationIndexStore(output_root)
        verified_first = store.load(first.manifest.index_version)
        _write_corpus(index_path.parent, include_second_rule=False)
        child = RelationIndexBuilder().build(
            index_path, output_root, parent_snapshot=first
        )
        edges_path = _snapshot_path(
            output_root, child.manifest.index_version, "edges.json"
        )
        edges = json.loads(edges_path.read_text(encoding="utf-8"))
        edges[0]["target_node_id"] = "rule:RG-MISSING-999"
        raw_edges = _write_canonical_json(edges_path, edges)
        _rewrite_artifact_digest(
            output_root,
            child.manifest.index_version,
            artifact_name="edges",
            raw=raw_edges,
        )

        with pytest.raises(RelationIndexIntegrityError):
            store.load(child.manifest.index_version)
        assert store.load(first.manifest.index_version) == verified_first

        child_manifest = _snapshot_path(
            output_root, child.manifest.index_version, "manifest.json"
        )
        manifest = json.loads(child_manifest.read_text(encoding="utf-8"))
        manifest["parent_index_version"] = "missing-parent"
        _write_canonical_json(child_manifest, manifest)

        with pytest.raises(RelationIndexIntegrityError):
            store.load(child.manifest.index_version)
