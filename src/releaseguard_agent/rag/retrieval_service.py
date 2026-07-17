from dataclasses import dataclass
from pathlib import Path

from llama_index.core.embeddings import BaseEmbedding

from releaseguard_agent.models.retrieval_evidence import RetrievalEvidence
from releaseguard_agent.rag.corpus import RuleCorpusLoader
from releaseguard_agent.rag.hybrid_retriever import (
    BM25RuleRetriever,
    ExactRuleRetriever,
    HybridRuleRetriever,
)
from releaseguard_agent.rag.vector_retriever import LlamaIndexVectorRetriever


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    requested_mode: str
    mode_used: str
    degraded_reason: str | None
    evidence: tuple[RetrievalEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "requested_mode": self.requested_mode,
            "mode_used": self.mode_used,
            "degraded_reason": self.degraded_reason,
            "evidence": [item.to_dict() for item in self.evidence],
        }


class RuleRetrievalService:
    """Reachable exact, BM25, vector, and hybrid rule retrieval service."""

    def __init__(
        self,
        index_path: Path,
        *,
        embed_model: BaseEmbedding | None = None,
    ) -> None:
        chunks = RuleCorpusLoader.from_rule_index(index_path)
        self._exact = ExactRuleRetriever(chunks)
        self._bm25 = BM25RuleRetriever(chunks)
        self._hybrid = HybridRuleRetriever()
        self._vector = (
            LlamaIndexVectorRetriever(chunks, embed_model=embed_model)
            if embed_model is not None
            else None
        )

    def retrieve(
        self,
        query: str,
        *,
        mode: str = "hybrid",
        top_k: int = 5,
    ) -> RetrievalResult:
        if top_k <= 0:
            raise ValueError("top_k must be positive.")
        normalized_mode = mode.strip().lower()
        if normalized_mode == "exact":
            evidence = self._exact.retrieve(query, top_k=top_k)
            return RetrievalResult(query, mode, "exact", None, tuple(evidence))
        if normalized_mode == "bm25":
            evidence = self._bm25.retrieve(query, top_k=top_k)
            return RetrievalResult(query, mode, "bm25", None, tuple(evidence))
        if normalized_mode not in {"vector", "hybrid"}:
            raise ValueError(f"Unsupported retrieval mode: {mode!r}.")
        bm25 = self._bm25.retrieve(query, top_k=max(top_k * 2, top_k))
        if self._vector is None:
            return RetrievalResult(
                query,
                mode,
                "bm25",
                "embedding_unavailable",
                tuple(bm25[:top_k]),
            )
        vector = self._vector.retrieve(query, top_k=max(top_k * 2, top_k))
        if normalized_mode == "vector":
            return RetrievalResult(query, mode, "vector", None, tuple(vector[:top_k]))
        fused = self._hybrid.fuse(query, [bm25, vector], top_k=top_k)
        return RetrievalResult(query, mode, "hybrid", None, tuple(fused))
