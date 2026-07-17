import re
from collections.abc import Iterable

from rank_bm25 import BM25Okapi  # type: ignore[import-untyped]

from releaseguard_agent.models.retrieval_evidence import RetrievalEvidence
from releaseguard_agent.rag.corpus import RuleChunk


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_-]+", text.lower())


class BM25RuleRetriever:
    def __init__(self, chunks: Iterable[RuleChunk]) -> None:
        self._chunks = tuple(chunks)
        self._index = BM25Okapi([tokenize(chunk.text) for chunk in self._chunks])

    def retrieve(self, query: str, *, top_k: int = 5) -> list[RetrievalEvidence]:
        scores = self._index.get_scores(tokenize(query))
        ranked = sorted(
            zip(self._chunks, scores, strict=True),
            key=lambda item: (-float(item[1]), item[0].chunk_id),
        )[:top_k]
        return [_evidence(chunk, "bm25", float(score)) for chunk, score in ranked]


class ExactRuleRetriever:
    def __init__(self, chunks: Iterable[RuleChunk]) -> None:
        self._chunks = tuple(chunks)

    def retrieve(self, rule_id: str, *, top_k: int = 5) -> list[RetrievalEvidence]:
        return [
            _evidence(chunk, "exact", 1.0)
            for chunk in self._chunks
            if chunk.rule_id == rule_id.strip()
        ][:top_k]


class HybridRuleRetriever:
    """Fuse, deduplicate, and deterministically rerank retrieval candidates."""

    def fuse(
        self,
        query: str,
        candidate_lists: Iterable[list[RetrievalEvidence]],
        *,
        top_k: int = 5,
    ) -> list[RetrievalEvidence]:
        by_chunk: dict[str, RetrievalEvidence] = {}
        fusion: dict[str, float] = {}
        methods: dict[str, set[str]] = {}
        for candidates in candidate_lists:
            for rank, item in enumerate(candidates, start=1):
                by_chunk.setdefault(item.chunk_id, item)
                fusion[item.chunk_id] = fusion.get(item.chunk_id, 0.0) + 1 / (60 + rank)
                methods.setdefault(item.chunk_id, set()).add(item.retrieval_method)
        query_tokens = set(tokenize(query))
        ranked: list[RetrievalEvidence] = []
        for chunk_id, item in by_chunk.items():
            overlap = len(query_tokens.intersection(tokenize(item.text)))
            rerank = fusion[chunk_id] + overlap * 0.01
            ranked.append(
                RetrievalEvidence(
                    evidence_id=item.evidence_id,
                    rule_id=item.rule_id,
                    source_url=item.source_url,
                    local_source=item.local_source,
                    chunk_id=item.chunk_id,
                    retrieval_method="+".join(sorted(methods[chunk_id])),
                    raw_score=item.raw_score,
                    fusion_score=fusion[chunk_id],
                    rerank_score=rerank,
                    text=item.text,
                    metadata=dict(item.metadata),
                )
            )
        return sorted(ranked, key=lambda item: (-item.rerank_score, item.chunk_id))[:top_k]


def _evidence(chunk: RuleChunk, method: str, score: float) -> RetrievalEvidence:
    return RetrievalEvidence(
        evidence_id=f"EVID-{chunk.chunk_id}",
        rule_id=chunk.rule_id,
        source_url=chunk.source_url,
        local_source=chunk.local_source,
        chunk_id=chunk.chunk_id,
        retrieval_method=method,
        raw_score=score,
        fusion_score=0.0,
        rerank_score=0.0,
        text=chunk.text,
        metadata=dict(chunk.metadata),
    )
