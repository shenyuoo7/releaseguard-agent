"""Build and verify immutable relation snapshots from the trusted rule corpus."""

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from releaseguard_agent.models.relation_index import (
    RelationChangeSet,
    RelationEdge,
    RelationEdgeType,
    RelationNode,
    RelationNodeType,
    RelationSnapshot,
    RelationSnapshotManifest,
)
from releaseguard_agent.rag.corpus import RuleChunk, RuleCorpusLoader
from releaseguard_agent.rag.rule_index_retriever import RuleIndexRetriever


_SCHEMA_VERSION = "1"
_CHUNKING_CONFIG = (("loader", "RuleCorpusLoader.from_rule_index"),)
_RETRIEVAL_CONFIG = (("relation_snapshot", "trusted-corpus-only"),)


class RelationIndexIntegrityError(ValueError):
    """Raised when an immutable relation snapshot fails closed validation."""


class RelationIndexBuilder:
    """Create atomically published relation snapshots from trusted rule inputs."""

    def build(
        self,
        rule_index_path: Path,
        output_root: Path,
        *,
        parent_snapshot: RelationSnapshot | None = None,
    ) -> RelationSnapshot:
        """Build or return the content-addressed snapshot for the current corpus."""
        normalized_index_path = Path(rule_index_path)
        normalized_output_root = Path(output_root)
        retriever = RuleIndexRetriever.from_file(normalized_index_path)
        chunks = RuleCorpusLoader.from_rule_index(normalized_index_path)
        source_index_sha256 = _source_index_digest(retriever, chunks)
        parent = _resolve_parent(parent_snapshot, normalized_output_root)
        parent_sha256 = "" if parent is None else parent.snapshot_sha256
        index_version = _index_version(source_index_sha256, parent_sha256)
        store = RelationIndexStore(normalized_output_root)
        output_path = normalized_output_root / index_version

        if output_path.exists():
            return store.load(index_version)

        nodes, edges = _build_relations(retriever, chunks)
        change_set = _build_change_set(parent, nodes, edges)
        manifest = RelationSnapshotManifest(
            schema_version=_SCHEMA_VERSION,
            index_version=index_version,
            source_index_sha256=source_index_sha256,
            chunking_config=_CHUNKING_CONFIG,
            retrieval_config=_RETRIEVAL_CONFIG,
            created_at_utc=datetime.now(UTC).isoformat(),
            parent_index_version=(
                None if parent is None else parent.manifest.index_version
            ),
            change_set=change_set,
            nodes_sha256=_sha256(_canonical_bytes([node.to_dict() for node in nodes])),
            edges_sha256=_sha256(_canonical_bytes([edge.to_dict() for edge in edges])),
        )
        _validate_snapshot(manifest, nodes, edges, parent)
        _publish_snapshot(normalized_output_root, manifest, nodes, edges)
        return store.load(index_version)


class RelationIndexStore:
    """Fail-closed reader for immutable relation snapshot directories."""

    def __init__(self, output_root: Path) -> None:
        self._output_root = Path(output_root)

    def load(self, index_version: str) -> RelationSnapshot:
        """Read one version after validating bytes, provenance, and topology."""
        if not index_version or Path(index_version).name != index_version:
            raise RelationIndexIntegrityError("Invalid relation index version.")
        return self._load(index_version, seen_versions=())

    def _load(
        self,
        index_version: str,
        *,
        seen_versions: tuple[str, ...],
    ) -> RelationSnapshot:
        if index_version in seen_versions:
            raise RelationIndexIntegrityError("Relation snapshot parent cycle detected.")
        version_root = self._output_root / index_version
        manifest_raw = _read_artifact(version_root / "manifest.json")
        nodes_raw = _read_artifact(version_root / "nodes.json")
        edges_raw = _read_artifact(version_root / "edges.json")
        manifest = _manifest_from_bytes(manifest_raw)
        nodes = _nodes_from_bytes(nodes_raw)
        edges = _edges_from_bytes(edges_raw)

        if manifest.index_version != index_version:
            raise RelationIndexIntegrityError("Manifest index version does not match path.")
        if _sha256(nodes_raw) != manifest.nodes_sha256:
            raise RelationIndexIntegrityError("Relation node digest mismatch.")
        if _sha256(edges_raw) != manifest.edges_sha256:
            raise RelationIndexIntegrityError("Relation edge digest mismatch.")

        parent: RelationSnapshot | None = None
        if manifest.parent_index_version is not None:
            parent = self._load(
                manifest.parent_index_version,
                seen_versions=(*seen_versions, index_version),
            )
        expected_version = _index_version(
            manifest.source_index_sha256,
            "" if parent is None else parent.snapshot_sha256,
        )
        if expected_version != manifest.index_version:
            raise RelationIndexIntegrityError("Relation snapshot parent binding failed.")
        _validate_snapshot(manifest, nodes, edges, parent)
        return RelationSnapshot(
            manifest=manifest,
            nodes=nodes,
            edges=edges,
            snapshot_sha256=_snapshot_digest(manifest, nodes, edges),
        )


def _source_index_digest(
    retriever: RuleIndexRetriever,
    chunks: tuple[RuleChunk, ...],
) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "records": [record.to_dict() for record in retriever.records],
                "chunks": [
                    {
                        "chunk_id": chunk.chunk_id,
                        "rule_id": chunk.rule_id,
                        "text": chunk.text,
                        "source_url": chunk.source_url,
                        "local_source": chunk.local_source,
                        "metadata": dict(sorted(chunk.metadata.items())),
                    }
                    for chunk in sorted(chunks, key=lambda item: item.chunk_id)
                ],
            }
        )
    )


def _resolve_parent(
    parent_snapshot: RelationSnapshot | None,
    output_root: Path,
) -> RelationSnapshot | None:
    if parent_snapshot is None:
        return None
    expected_sha256 = _snapshot_digest(
        parent_snapshot.manifest,
        parent_snapshot.nodes,
        parent_snapshot.edges,
    )
    if parent_snapshot.snapshot_sha256 != expected_sha256:
        raise RelationIndexIntegrityError("Parent snapshot digest is invalid.")
    stored_parent = RelationIndexStore(output_root).load(
        parent_snapshot.manifest.index_version
    )
    if stored_parent.snapshot_sha256 != parent_snapshot.snapshot_sha256:
        raise RelationIndexIntegrityError("Parent snapshot does not match published data.")
    return stored_parent


def _build_relations(
    retriever: RuleIndexRetriever,
    chunks: tuple[RuleChunk, ...],
) -> tuple[tuple[RelationNode, ...], tuple[RelationEdge, ...]]:
    chunks_by_rule: dict[str, list[RuleChunk]] = {}
    for chunk in chunks:
        chunks_by_rule.setdefault(chunk.rule_id, []).append(chunk)
    node_data: dict[str, tuple[RelationNodeType, str, str, set[str]]] = {}
    edge_data: dict[tuple[str, str, RelationEdgeType], set[str]] = {}

    def add_node(
        node_id: str,
        node_type: RelationNodeType,
        canonical_name: str,
        description: str,
        source_chunk_ids: tuple[str, ...],
    ) -> None:
        existing = node_data.get(node_id)
        if existing is None:
            node_data[node_id] = (
                node_type,
                canonical_name,
                description,
                set(source_chunk_ids),
            )
            return
        if existing[:3] != (node_type, canonical_name, description):
            raise RelationIndexIntegrityError(
                f"Conflicting trusted node data for {node_id!r}."
            )
        existing[3].update(source_chunk_ids)

    def add_edge(
        source_node_id: str,
        target_node_id: str,
        relation_type: RelationEdgeType,
        source_chunk_ids: tuple[str, ...],
    ) -> None:
        edge_data.setdefault(
            (source_node_id, target_node_id, relation_type), set()
        ).update(source_chunk_ids)

    for record in sorted(retriever.records, key=lambda item: item.rule_id):
        rule_chunks = tuple(sorted(chunks_by_rule.get(record.rule_id, ()), key=lambda item: item.chunk_id))
        chunk_ids = tuple(chunk.chunk_id for chunk in rule_chunks)
        rule_node_id = f"rule:{record.rule_id}"
        checker_node_id = f"checker:{record.checker}"
        phase_node_id = f"phase:{record.phase}"
        add_node(
            rule_node_id,
            RelationNodeType.RULE,
            record.rule_id,
            record.rule_name,
            chunk_ids,
        )
        add_node(
            checker_node_id,
            RelationNodeType.CHECKER,
            record.checker,
            record.checker,
            chunk_ids,
        )
        add_node(
            phase_node_id,
            RelationNodeType.PHASE,
            record.phase,
            record.phase,
            chunk_ids,
        )
        add_edge(
            rule_node_id,
            checker_node_id,
            RelationEdgeType.RULE_CHECKED_BY,
            chunk_ids,
        )
        add_edge(
            rule_node_id,
            phase_node_id,
            RelationEdgeType.RULE_APPLIES_TO_PHASE,
            chunk_ids,
        )
        for chunk in rule_chunks:
            chunk_node_id = f"chunk:{chunk.chunk_id}"
            source_node_id = _source_node_id(chunk.source_url, chunk.local_source)
            source_name = chunk.source_url or chunk.local_source
            add_node(
                chunk_node_id,
                RelationNodeType.CHUNK,
                chunk.chunk_id,
                chunk.text,
                (chunk.chunk_id,),
            )
            add_node(
                source_node_id,
                RelationNodeType.SOURCE,
                source_name,
                chunk.local_source,
                (chunk.chunk_id,),
            )
            add_edge(
                rule_node_id,
                source_node_id,
                RelationEdgeType.RULE_SOURCED_BY,
                (chunk.chunk_id,),
            )
            add_edge(
                rule_node_id,
                chunk_node_id,
                RelationEdgeType.RULE_HAS_CHUNK,
                (chunk.chunk_id,),
            )

    nodes = tuple(
        RelationNode(
            node_id=node_id,
            node_type=node_type,
            canonical_name=canonical_name,
            description=description,
            source_chunk_ids=tuple(sorted(source_chunk_ids)),
        )
        for node_id, (node_type, canonical_name, description, source_chunk_ids)
        in sorted(node_data.items())
    )
    unsorted_edges = (
        RelationEdge(
            edge_id=_edge_id(source_node_id, target_node_id, relation_type),
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relation_type=relation_type,
            source_chunk_ids=tuple(sorted(source_chunk_ids)),
        )
        for (source_node_id, target_node_id, relation_type), source_chunk_ids
        in sorted(
            edge_data.items(),
            key=lambda item: (item[0][0], item[0][1], item[0][2].value),
        )
    )
    edges = tuple(sorted(unsorted_edges, key=lambda edge: edge.edge_id))
    return nodes, edges


def _build_change_set(
    parent: RelationSnapshot | None,
    nodes: tuple[RelationNode, ...],
    edges: tuple[RelationEdge, ...],
) -> RelationChangeSet:
    parent_nodes = {} if parent is None else {node.node_id: node for node in parent.nodes}
    parent_edges = {} if parent is None else {edge.edge_id: edge for edge in parent.edges}
    current_nodes = {node.node_id: node for node in nodes}
    current_edges = {edge.edge_id: edge for edge in edges}
    return RelationChangeSet(
        added_node_ids=tuple(sorted(current_nodes.keys() - parent_nodes.keys())),
        updated_node_ids=tuple(
            sorted(
                node_id
                for node_id in current_nodes.keys() & parent_nodes.keys()
                if current_nodes[node_id] != parent_nodes[node_id]
            )
        ),
        tombstoned_node_ids=tuple(sorted(parent_nodes.keys() - current_nodes.keys())),
        added_edge_ids=tuple(sorted(current_edges.keys() - parent_edges.keys())),
        updated_edge_ids=tuple(
            sorted(
                edge_id
                for edge_id in current_edges.keys() & parent_edges.keys()
                if current_edges[edge_id] != parent_edges[edge_id]
            )
        ),
        tombstoned_edge_ids=tuple(sorted(parent_edges.keys() - current_edges.keys())),
    )


def _publish_snapshot(
    output_root: Path,
    manifest: RelationSnapshotManifest,
    nodes: tuple[RelationNode, ...],
    edges: tuple[RelationEdge, ...],
) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    final_path = output_root / manifest.index_version
    temporary_path = Path(tempfile.mkdtemp(prefix=".relation-index-", dir=output_root))
    try:
        (temporary_path / "manifest.json").write_bytes(
            _canonical_bytes(manifest.to_dict())
        )
        (temporary_path / "nodes.json").write_bytes(
            _canonical_bytes([node.to_dict() for node in nodes])
        )
        (temporary_path / "edges.json").write_bytes(
            _canonical_bytes([edge.to_dict() for edge in edges])
        )
        try:
            os.replace(temporary_path, final_path)
        except FileExistsError:
            RelationIndexStore(output_root).load(manifest.index_version)
    finally:
        if temporary_path.exists():
            shutil.rmtree(temporary_path)


def _read_artifact(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise RelationIndexIntegrityError(
            f"Required relation artifact is unavailable: {path.name}."
        ) from error


def _manifest_from_bytes(raw: bytes) -> RelationSnapshotManifest:
    value = _canonical_json_value(raw, "manifest")
    if not isinstance(value, dict):
        raise RelationIndexIntegrityError("Relation manifest must be an object.")
    try:
        _exact_keys(
            value,
            {
                "schema_version",
                "index_version",
                "source_index_sha256",
                "chunking_config",
                "retrieval_config",
                "created_at_utc",
                "parent_index_version",
                "change_set",
                "nodes_sha256",
                "edges_sha256",
            },
            "manifest",
        )
        change_set_value = _required_mapping(value, "change_set")
        _exact_keys(
            change_set_value,
            {
                "added_node_ids",
                "updated_node_ids",
                "tombstoned_node_ids",
                "added_edge_ids",
                "updated_edge_ids",
                "tombstoned_edge_ids",
            },
            "change set",
        )
        return RelationSnapshotManifest(
            schema_version=_required_string(value, "schema_version"),
            index_version=_required_string(value, "index_version"),
            source_index_sha256=_required_string(value, "source_index_sha256"),
            chunking_config=_string_pairs(value, "chunking_config"),
            retrieval_config=_string_pairs(value, "retrieval_config"),
            created_at_utc=_required_string(value, "created_at_utc"),
            parent_index_version=_optional_string(value, "parent_index_version"),
            change_set=RelationChangeSet(
                added_node_ids=_string_tuple(change_set_value, "added_node_ids"),
                updated_node_ids=_string_tuple(change_set_value, "updated_node_ids"),
                tombstoned_node_ids=_string_tuple(change_set_value, "tombstoned_node_ids"),
                added_edge_ids=_string_tuple(change_set_value, "added_edge_ids"),
                updated_edge_ids=_string_tuple(change_set_value, "updated_edge_ids"),
                tombstoned_edge_ids=_string_tuple(change_set_value, "tombstoned_edge_ids"),
            ),
            nodes_sha256=_required_string(value, "nodes_sha256"),
            edges_sha256=_required_string(value, "edges_sha256"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RelationIndexIntegrityError("Relation manifest is invalid.") from error


def _nodes_from_bytes(raw: bytes) -> tuple[RelationNode, ...]:
    value = _canonical_json_value(raw, "nodes")
    if not isinstance(value, list):
        raise RelationIndexIntegrityError("Relation nodes must be a list.")
    try:
        if any(not isinstance(item, dict) for item in value):
            raise TypeError("node")
        for item in value:
            _exact_keys(
                item,
                {
                    "node_id",
                    "node_type",
                    "canonical_name",
                    "description",
                    "source_chunk_ids",
                },
                "node",
            )
        return tuple(
            RelationNode(
                node_id=_required_string(item, "node_id"),
                node_type=RelationNodeType(_required_string(item, "node_type")),
                canonical_name=_required_string(item, "canonical_name"),
                description=_required_string(item, "description"),
                source_chunk_ids=_string_tuple(item, "source_chunk_ids"),
            )
            for item in value
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RelationIndexIntegrityError("Relation node is invalid.") from error


def _edges_from_bytes(raw: bytes) -> tuple[RelationEdge, ...]:
    value = _canonical_json_value(raw, "edges")
    if not isinstance(value, list):
        raise RelationIndexIntegrityError("Relation edges must be a list.")
    try:
        if any(not isinstance(item, dict) for item in value):
            raise TypeError("edge")
        for item in value:
            _exact_keys(
                item,
                {
                    "edge_id",
                    "source_node_id",
                    "target_node_id",
                    "relation_type",
                    "source_chunk_ids",
                },
                "edge",
            )
        return tuple(
            RelationEdge(
                edge_id=_required_string(item, "edge_id"),
                source_node_id=_required_string(item, "source_node_id"),
                target_node_id=_required_string(item, "target_node_id"),
                relation_type=RelationEdgeType(
                    _required_string(item, "relation_type")
                ),
                source_chunk_ids=_string_tuple(item, "source_chunk_ids"),
            )
            for item in value
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RelationIndexIntegrityError("Relation edge is invalid.") from error


def _canonical_json_value(raw: bytes, label: str) -> Any:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RelationIndexIntegrityError(f"Relation {label} JSON is invalid.") from error
    if _canonical_bytes(value) != raw:
        raise RelationIndexIntegrityError(f"Relation {label} JSON is not canonical.")
    return value


def _validate_snapshot(
    manifest: RelationSnapshotManifest,
    nodes: tuple[RelationNode, ...],
    edges: tuple[RelationEdge, ...],
    parent: RelationSnapshot | None,
) -> None:
    if manifest.schema_version != _SCHEMA_VERSION:
        raise RelationIndexIntegrityError("Unsupported relation snapshot schema.")
    if not _is_sha256(manifest.source_index_sha256):
        raise RelationIndexIntegrityError("Invalid source index digest.")
    if not _is_sha256(manifest.nodes_sha256) or not _is_sha256(manifest.edges_sha256):
        raise RelationIndexIntegrityError("Invalid relation artifact digest.")
    if not manifest.created_at_utc:
        raise RelationIndexIntegrityError("Missing relation snapshot creation time.")
    if parent is None and manifest.parent_index_version is not None:
        raise RelationIndexIntegrityError("Relation parent snapshot is unavailable.")
    if parent is not None and manifest.parent_index_version != parent.manifest.index_version:
        raise RelationIndexIntegrityError("Relation parent version does not match.")
    _validate_sorted_unique(
        (node.node_id for node in nodes), "relation node identifiers"
    )
    _validate_sorted_unique(
        (edge.edge_id for edge in edges), "relation edge identifiers"
    )
    known_chunk_ids = {
        node.canonical_name
        for node in nodes
        if node.node_type is RelationNodeType.CHUNK
    }
    for node in nodes:
        if not node.node_id or not node.canonical_name or not node.description:
            raise RelationIndexIntegrityError("Relation node has empty required fields.")
        _validate_sorted_unique(node.source_chunk_ids, "node source chunk identifiers")
        if not set(node.source_chunk_ids).issubset(known_chunk_ids):
            raise RelationIndexIntegrityError("Node has unknown source chunk provenance.")
    node_ids = {node.node_id for node in nodes}
    semantic_edges: set[tuple[str, str, RelationEdgeType]] = set()
    for edge in edges:
        if not edge.edge_id:
            raise RelationIndexIntegrityError("Relation edge identifier is empty.")
        if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
            raise RelationIndexIntegrityError("Relation edge has a dangling endpoint.")
        _validate_sorted_unique(edge.source_chunk_ids, "edge source chunk identifiers")
        if not edge.source_chunk_ids or not set(edge.source_chunk_ids).issubset(known_chunk_ids):
            raise RelationIndexIntegrityError("Edge has invalid source chunk provenance.")
        semantic_key = (
            edge.source_node_id,
            edge.target_node_id,
            edge.relation_type,
        )
        if semantic_key in semantic_edges:
            raise RelationIndexIntegrityError("Duplicate relation edge is not allowed.")
        semantic_edges.add(semantic_key)


def _validate_sorted_unique(values: Any, label: str) -> None:
    items = tuple(values)
    if any(not isinstance(item, str) or not item for item in items):
        raise RelationIndexIntegrityError(f"Invalid {label}.")
    if items != tuple(sorted(set(items))):
        raise RelationIndexIntegrityError(f"{label.capitalize()} must be sorted and unique.")


def _snapshot_digest(
    manifest: RelationSnapshotManifest,
    nodes: tuple[RelationNode, ...],
    edges: tuple[RelationEdge, ...],
) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "manifest": manifest.to_dict(),
                "nodes": [node.to_dict() for node in nodes],
                "edges": [edge.to_dict() for edge in edges],
            }
        )
    )


def _source_node_id(source_url: str, local_source: str) -> str:
    return "source:" + _sha256(
        _canonical_bytes({"source_url": source_url, "local_source": local_source})
    )[:24]


def _edge_id(
    source_node_id: str,
    target_node_id: str,
    relation_type: RelationEdgeType,
) -> str:
    return "edge:" + _sha256(
        _canonical_bytes(
            {
                "source_node_id": source_node_id,
                "target_node_id": target_node_id,
                "relation_type": relation_type.value,
            }
        )
    )[:24]


def _index_version(source_index_sha256: str, parent_snapshot_sha256: str) -> str:
    return "ri-" + _sha256(
        _canonical_bytes(
            {
                "schema_version": _SCHEMA_VERSION,
                "source_index_sha256": source_index_sha256,
                "parent_snapshot_sha256": parent_snapshot_sha256,
            }
        )
    )[:24]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _required_mapping(value: dict[str, object], field_name: str) -> dict[str, object]:
    field_value = value[field_name]
    if not isinstance(field_value, dict):
        raise TypeError(field_name)
    return field_value


def _exact_keys(
    value: dict[str, object],
    expected: set[str],
    label: str,
) -> None:
    if set(value) != expected:
        raise ValueError(f"Invalid relation {label} fields.")


def _required_string(value: dict[str, object], field_name: str) -> str:
    field_value = value[field_name]
    if not isinstance(field_value, str) or not field_value:
        raise TypeError(field_name)
    return field_value


def _optional_string(value: dict[str, object], field_name: str) -> str | None:
    field_value = value[field_name]
    if field_value is None:
        return None
    if not isinstance(field_value, str) or not field_value:
        raise TypeError(field_name)
    return field_value


def _string_pairs(value: dict[str, object], field_name: str) -> tuple[tuple[str, str], ...]:
    mapping = _required_mapping(value, field_name)
    pairs: list[tuple[str, str]] = []
    for key, item in mapping.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise TypeError(field_name)
        pairs.append((key, item))
    return tuple(sorted(pairs))


def _string_tuple(value: dict[str, object], field_name: str) -> tuple[str, ...]:
    field_value = value[field_name]
    if not isinstance(field_value, list) or any(
        not isinstance(item, str) or not item for item in field_value
    ):
        raise TypeError(field_name)
    result = tuple(field_value)
    if result != tuple(sorted(set(result))):
        raise ValueError(field_name)
    return result
