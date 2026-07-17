from releaseguard_agent import rag
from releaseguard_agent.rag import (
    CheckResultEnricher,
    EnrichedCheckResult,
    RuleIndexFormatError,
    RuleIndexRetriever,
    RuleNotFoundError,
)


EXPECTED_PUBLIC_API = {
    "BM25RuleRetriever",
    "CheckResultEnricher",
    "EnrichedCheckResult",
    "ExactRuleRetriever",
    "HybridRuleRetriever",
    "LlamaIndexVectorRetriever",
    "RetrievalResult",
    "RuleChunk",
    "RuleCorpusLoader",
    "RuleIndexFormatError",
    "RuleIndexRetriever",
    "RuleNotFoundError",
    "RuleRetrievalService",
    "build_embedding_model",
    "get_default_rule_index_path",
}


def test_rag_public_api_exports_expected_names() -> None:
    assert set(rag.__all__) == EXPECTED_PUBLIC_API


def test_rag_public_api_imports_representative_types() -> None:
    assert callable(CheckResultEnricher)
    assert callable(EnrichedCheckResult)
    assert issubclass(RuleIndexFormatError, ValueError)
    assert callable(RuleIndexRetriever)
    assert issubclass(RuleNotFoundError, LookupError)
