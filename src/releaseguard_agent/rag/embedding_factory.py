from collections.abc import Callable, Mapping

from llama_index.core.embeddings import BaseEmbedding
from llama_index.embeddings.openai import OpenAIEmbedding  # type: ignore[import-untyped]


EmbeddingBuilder = Callable[..., BaseEmbedding]


def build_embedding_model(
    environment: Mapping[str, str],
    *,
    builder: EmbeddingBuilder = OpenAIEmbedding,
) -> BaseEmbedding | None:
    """Build the configured real embedding model or return offline fallback."""
    provider = environment.get(
        "RELEASEGUARD_EMBEDDING_PROVIDER", "disabled"
    ).strip().lower()
    if provider in {"", "disabled", "none"}:
        return None
    if provider not in {"openai", "openai-compatible"}:
        raise ValueError(f"Unsupported embedding provider: {provider!r}.")
    api_key = environment.get("RELEASEGUARD_EMBEDDING_API_KEY", "").strip()
    model = environment.get("RELEASEGUARD_EMBEDDING_MODEL", "").strip()
    if not api_key:
        return None
    if not model:
        raise ValueError(
            "RELEASEGUARD_EMBEDDING_MODEL is required when vector retrieval is enabled."
        )
    kwargs: dict[str, object] = {"api_key": api_key, "model": model}
    base_url = environment.get("RELEASEGUARD_EMBEDDING_BASE_URL", "").strip()
    if base_url:
        kwargs["api_base"] = base_url
    return builder(**kwargs)
