from dataclasses import dataclass


@dataclass(frozen=True)
class RelationPath:
    """One bounded, verified path through an immutable relation snapshot."""

    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    source_chunk_ids: tuple[str, ...]
    index_version: str
    hop_count: int
    path_score: float

    def to_dict(self) -> dict[str, object]:
        return {
            "node_ids": list(self.node_ids),
            "edge_ids": list(self.edge_ids),
            "source_chunk_ids": list(self.source_chunk_ids),
            "index_version": self.index_version,
            "hop_count": self.hop_count,
            "path_score": self.path_score,
        }


@dataclass(frozen=True)
class RetrievalEvidence:
    """One traceable rule chunk returned by any retrieval method."""

    evidence_id: str
    rule_id: str
    source_url: str
    local_source: str
    chunk_id: str
    retrieval_method: str
    raw_score: float
    fusion_score: float
    rerank_score: float
    text: str
    metadata: dict[str, str]
    relation_paths: tuple[RelationPath, ...] = ()
    index_version: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "rule_id": self.rule_id,
            "source_url": self.source_url,
            "local_source": self.local_source,
            "chunk_id": self.chunk_id,
            "retrieval_method": self.retrieval_method,
            "raw_score": self.raw_score,
            "fusion_score": self.fusion_score,
            "rerank_score": self.rerank_score,
            "text": self.text,
            "metadata": dict(self.metadata),
            "relation_paths": [path.to_dict() for path in self.relation_paths],
            "index_version": self.index_version,
        }
