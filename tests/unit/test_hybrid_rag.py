from pathlib import Path

from llama_index.core.embeddings import BaseEmbedding

from releaseguard_agent.rag import (
    BM25RuleRetriever,
    ExactRuleRetriever,
    HybridRuleRetriever,
    LlamaIndexVectorRetriever,
    RuleCorpusLoader,
    RuleRetrievalService,
    build_embedding_model,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = PROJECT_ROOT / "knowledge_base" / "release_rules" / "rule_index.md"


class FixedEmbedding(BaseEmbedding):
    def _vector(self, text: str) -> list[float]:
        normalized = text.lower()
        return [
            float(normalized.count("docker")),
            float(normalized.count("pytest") + normalized.count("test")),
            float(normalized.count("fastapi")),
            1.0,
        ]

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._vector(text)


def test_corpus_chunks_preserve_rule_source_and_metadata() -> None:
    chunks = RuleCorpusLoader.from_rule_index(INDEX_PATH)
    chunk = next(item for item in chunks if item.rule_id == "RG-DOCKER-002")

    assert chunk.chunk_id.startswith("RG-DOCKER-002:chunk-")
    assert chunk.source_url.startswith("https://")
    assert chunk.local_source.endswith("dockerfile_reference.md")
    assert chunk.metadata["blocking_policy"] == "block"


def test_exact_and_bm25_return_complete_traceable_evidence() -> None:
    chunks = RuleCorpusLoader.from_rule_index(INDEX_PATH)
    exact = ExactRuleRetriever(chunks).retrieve("RG-TEST-005", top_k=2)
    bm25 = BM25RuleRetriever(chunks).retrieve("pytest run succeeds", top_k=3)

    assert exact and all(item.rule_id == "RG-TEST-005" for item in exact)
    assert bm25[0].raw_score >= bm25[-1].raw_score
    required = {
        "evidence_id",
        "rule_id",
        "source_url",
        "local_source",
        "chunk_id",
        "retrieval_method",
        "raw_score",
        "fusion_score",
        "rerank_score",
    }
    assert required.issubset(bm25[0].to_dict())


def test_llamaindex_vector_retrieval_uses_injected_embedding() -> None:
    chunks = RuleCorpusLoader.from_rule_index(INDEX_PATH)
    vector = LlamaIndexVectorRetriever(
        chunks,
        embed_model=FixedEmbedding(),
    )

    results = vector.retrieve("docker image instructions", top_k=4)

    assert len(results) == 4
    assert all(item.retrieval_method == "vector" for item in results)
    assert any(item.rule_id.startswith("RG-DOCKER-") for item in results)


def test_hybrid_fusion_deduplicates_and_reranks() -> None:
    chunks = RuleCorpusLoader.from_rule_index(INDEX_PATH)
    bm25 = BM25RuleRetriever(chunks).retrieve("docker FROM", top_k=8)
    vector = LlamaIndexVectorRetriever(
        chunks,
        embed_model=FixedEmbedding(),
    ).retrieve("docker FROM", top_k=8)

    results = HybridRuleRetriever().fuse(
        "docker FROM",
        [bm25, vector],
        top_k=5,
    )

    assert len({item.chunk_id for item in results}) == len(results)
    assert results[0].rerank_score >= results[-1].rerank_score
    assert any("bm25" in item.retrieval_method for item in results)
    assert any("vector" in item.retrieval_method for item in results)


def test_retrieval_service_degrades_vector_and_hybrid_to_bm25() -> None:
    service = RuleRetrievalService(INDEX_PATH)

    for mode in ("vector", "hybrid"):
        result = service.retrieve("pytest collection", mode=mode, top_k=3)
        assert result.requested_mode == mode
        assert result.mode_used == "bm25"
        assert result.degraded_reason == "embedding_unavailable"
        assert len(result.evidence) == 3


def test_embedding_factory_is_lazy_and_configurable() -> None:
    assert build_embedding_model({}) is None
    captured = {}
    fake = FixedEmbedding()

    def builder(**kwargs):
        captured.update(kwargs)
        return fake

    result = build_embedding_model(
        {
            "RELEASEGUARD_EMBEDDING_PROVIDER": "openai-compatible",
            "RELEASEGUARD_EMBEDDING_API_KEY": "test-only-key",
            "RELEASEGUARD_EMBEDDING_MODEL": "embedding-model",
            "RELEASEGUARD_EMBEDDING_BASE_URL": "https://example.invalid/v1",
        },
        builder=builder,
    )

    assert result is fake
    assert captured == {
        "api_key": "test-only-key",
        "model": "embedding-model",
        "api_base": "https://example.invalid/v1",
    }
