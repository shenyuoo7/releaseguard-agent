from releaseguard_agent import rag
from releaseguard_agent.rag import (
    CheckResultEnricher,
    EnrichedCheckResult,
    RuleIndexFormatError,
    RuleIndexRetriever,
    RuleNotFoundError,
)


EXPECTED_PUBLIC_API = {
    "CheckResultEnricher",
    "EnrichedCheckResult",
    "RuleIndexFormatError",
    "RuleIndexRetriever",
    "RuleNotFoundError",
}


def test_rag_public_api_exports_expected_names() -> None:
    assert set(rag.__all__) == EXPECTED_PUBLIC_API


def test_rag_public_api_imports_representative_types() -> None:
    assert callable(CheckResultEnricher)
    assert callable(EnrichedCheckResult)
    assert issubclass(RuleIndexFormatError, ValueError)
    assert callable(RuleIndexRetriever)
    assert issubclass(RuleNotFoundError, LookupError)
