"""Immutable models for trusted release-rule relation snapshots."""

from dataclasses import dataclass
from enum import Enum


class RelationNodeType(str, Enum):
    """Closed set of relation-node kinds supported by the initial index."""

    RULE = "RULE"
    CHECKER = "CHECKER"
    PHASE = "PHASE"
    SOURCE = "SOURCE"
    CHUNK = "CHUNK"


class RelationEdgeType(str, Enum):
    """Closed set of trusted-corpus relation kinds."""

    RULE_CHECKED_BY = "RULE_CHECKED_BY"
    RULE_APPLIES_TO_PHASE = "RULE_APPLIES_TO_PHASE"
    RULE_SOURCED_BY = "RULE_SOURCED_BY"
    RULE_HAS_CHUNK = "RULE_HAS_CHUNK"


@dataclass(frozen=True)
class RelationNode:
    """One canonical entity derived from the trusted rule corpus."""

    node_id: str
    node_type: RelationNodeType
    canonical_name: str
    description: str
    source_chunk_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "canonical_name": self.canonical_name,
            "description": self.description,
            "source_chunk_ids": list(self.source_chunk_ids),
        }


@dataclass(frozen=True)
class RelationEdge:
    """One directed, provenance-carrying relation between trusted nodes."""

    edge_id: str
    source_node_id: str
    target_node_id: str
    relation_type: RelationEdgeType
    source_chunk_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "relation_type": self.relation_type.value,
            "source_chunk_ids": list(self.source_chunk_ids),
        }


@dataclass(frozen=True)
class RelationChangeSet:
    """Explicit delta from a parent snapshot, including tombstones."""

    added_node_ids: tuple[str, ...] = ()
    updated_node_ids: tuple[str, ...] = ()
    tombstoned_node_ids: tuple[str, ...] = ()
    added_edge_ids: tuple[str, ...] = ()
    updated_edge_ids: tuple[str, ...] = ()
    tombstoned_edge_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "added_node_ids": list(self.added_node_ids),
            "updated_node_ids": list(self.updated_node_ids),
            "tombstoned_node_ids": list(self.tombstoned_node_ids),
            "added_edge_ids": list(self.added_edge_ids),
            "updated_edge_ids": list(self.updated_edge_ids),
            "tombstoned_edge_ids": list(self.tombstoned_edge_ids),
        }


@dataclass(frozen=True)
class RelationSnapshotManifest:
    """Version and integrity metadata for one immutable relation snapshot."""

    schema_version: str
    index_version: str
    source_index_sha256: str
    chunking_config: tuple[tuple[str, str], ...]
    retrieval_config: tuple[tuple[str, str], ...]
    created_at_utc: str
    parent_index_version: str | None
    change_set: RelationChangeSet
    nodes_sha256: str
    edges_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "index_version": self.index_version,
            "source_index_sha256": self.source_index_sha256,
            "chunking_config": dict(self.chunking_config),
            "retrieval_config": dict(self.retrieval_config),
            "created_at_utc": self.created_at_utc,
            "parent_index_version": self.parent_index_version,
            "change_set": self.change_set.to_dict(),
            "nodes_sha256": self.nodes_sha256,
            "edges_sha256": self.edges_sha256,
        }


@dataclass(frozen=True)
class RelationSnapshot:
    """Fully verified immutable relation data and its derived digest."""

    manifest: RelationSnapshotManifest
    nodes: tuple[RelationNode, ...]
    edges: tuple[RelationEdge, ...]
    snapshot_sha256: str


@dataclass(frozen=True)
class RelationQueryBudget:
    """Hard traversal and context caps for later relation retrieval consumers."""

    max_hops: int
    max_nodes: int
    max_edges: int
    max_context_characters: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("max_hops", self.max_hops),
            ("max_nodes", self.max_nodes),
            ("max_edges", self.max_edges),
            ("max_context_characters", self.max_context_characters),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer.")
