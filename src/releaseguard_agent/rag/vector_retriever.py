from collections.abc import Iterable

from llama_index.core import Document, VectorStoreIndex
from llama_index.core.embeddings import BaseEmbedding

from releaseguard_agent.models.retrieval_evidence import RetrievalEvidence
from releaseguard_agent.rag.corpus import RuleChunk


class LlamaIndexVectorRetriever:
    """In-memory LlamaIndex vector retrieval with an injected embed model."""

    def __init__(
        self,
        chunks: Iterable[RuleChunk],
        *,
        embed_model: BaseEmbedding,
    ) -> None:
        self._chunks = {chunk.chunk_id: chunk for chunk in chunks}
        documents = [
            Document(
                text=chunk.text,
                id_=chunk.chunk_id,
                metadata={"chunk_id": chunk.chunk_id, "rule_id": chunk.rule_id},
            )
            for chunk in self._chunks.values()
        ]
        self._index = VectorStoreIndex.from_documents(
            documents,
            embed_model=embed_model,
            show_progress=False,
        )

    def retrieve(self, query: str, *, top_k: int = 5) -> list[RetrievalEvidence]:
        nodes = self._index.as_retriever(similarity_top_k=top_k).retrieve(query)
        evidence: list[RetrievalEvidence] = []
        for node in nodes:
            chunk_id = str(node.node.metadata["chunk_id"])
            chunk = self._chunks[chunk_id]
            evidence.append(
                RetrievalEvidence(
                    evidence_id=f"EVID-{chunk.chunk_id}",
                    rule_id=chunk.rule_id,
                    source_url=chunk.source_url,
                    local_source=chunk.local_source,
                    chunk_id=chunk.chunk_id,
                    retrieval_method="vector",
                    raw_score=float(node.score or 0.0),
                    fusion_score=0.0,
                    rerank_score=0.0,
                    text=chunk.text,
                    metadata=dict(chunk.metadata),
                )
            )
        return evidence
