from dataclasses import dataclass


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
        }
