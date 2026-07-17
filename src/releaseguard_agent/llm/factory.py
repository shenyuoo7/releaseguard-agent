from collections.abc import Callable, Mapping
from dataclasses import dataclass

from releaseguard_agent.llm.client import LLMClient
from releaseguard_agent.llm.openai_client import OpenAIChatCompletionClient


PROVIDER_ENV = "RELEASEGUARD_LLM_PROVIDER"
MODEL_ENV = "RELEASEGUARD_LLM_MODEL"
API_KEY_ENV = "RELEASEGUARD_LLM_API_KEY"
BASE_URL_ENV = "RELEASEGUARD_LLM_BASE_URL"
TIMEOUT_ENV = "RELEASEGUARD_LLM_TIMEOUT"


class LLMProviderConfigurationError(ValueError):
    """Raised for invalid non-secret provider configuration."""


@dataclass(frozen=True)
class LLMRuntime:
    """Resolved provider mode and optional production client."""

    mode: str
    provider: str
    model: str | None
    client: LLMClient | None
    fallback_reason: str | None = None

    @property
    def enabled(self) -> bool:
        return self.client is not None


ClientBuilder = Callable[..., LLMClient]


def build_llm_runtime(
    environment: Mapping[str, str],
    *,
    client_builder: ClientBuilder = OpenAIChatCompletionClient,
) -> LLMRuntime:
    """Build an OpenAI-compatible runtime or deterministic safe fallback."""
    provider = environment.get(PROVIDER_ENV, "deterministic").strip().lower()
    if provider in {"", "deterministic", "none", "disabled"}:
        return LLMRuntime(
            mode="deterministic",
            provider="deterministic",
            model=None,
            client=None,
        )
    if provider not in {"openai", "openai-compatible"}:
        raise LLMProviderConfigurationError(
            f"Unsupported LLM provider: {provider!r}."
        )

    model = _optional_value(environment.get(MODEL_ENV))
    api_key = _optional_value(environment.get(API_KEY_ENV))
    if api_key is None:
        return LLMRuntime(
            mode="deterministic",
            provider="deterministic",
            model=None,
            client=None,
            fallback_reason="missing_api_key",
        )
    if model is None:
        raise LLMProviderConfigurationError(
            f"{MODEL_ENV} is required when an LLM provider is enabled."
        )

    timeout = _parse_timeout(environment.get(TIMEOUT_ENV))
    client = client_builder(
        api_key=api_key,
        model=model,
        base_url=_optional_value(environment.get(BASE_URL_ENV)),
        timeout=timeout,
        provider_name="openai-compatible",
    )
    return LLMRuntime(
        mode="llm",
        provider="openai-compatible",
        model=model,
        client=client,
    )


def _optional_value(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()


def _parse_timeout(value: str | None) -> float | None:
    normalized = _optional_value(value)
    if normalized is None:
        return None
    try:
        timeout = float(normalized)
    except ValueError as exc:
        raise LLMProviderConfigurationError(
            f"{TIMEOUT_ENV} must be a positive number."
        ) from exc
    if timeout <= 0:
        raise LLMProviderConfigurationError(
            f"{TIMEOUT_ENV} must be a positive number."
        )
    return timeout
