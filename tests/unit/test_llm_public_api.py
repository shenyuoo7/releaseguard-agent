import releaseguard_agent.llm as llm
from releaseguard_agent.llm import (
    API_KEY_ENV,
    BASE_URL_ENV,
    LLM_MESSAGE_ROLES,
    FakeLLMClient,
    LLMCall,
    LLMClient,
    LLMMessage,
    LLMResponse,
    LLMProviderConfigurationError,
    LLMRuntime,
    MODEL_ENV,
    MissingFakeLLMResponseError,
    OpenAIChatCompletionClient,
    OpenAIClientConfigurationError,
    OpenAIClientDependencyError,
    OpenAIClientRequestError,
    OpenAIClientResponseError,
    PROVIDER_ENV,
    TIMEOUT_ENV,
    build_llm_runtime,
)


def test_llm_public_api_exports_provider_abstraction_helpers() -> None:
    assert LLM_MESSAGE_ROLES == (
        "system",
        "user",
        "assistant",
        "tool",
    )
    assert FakeLLMClient.__name__ == "FakeLLMClient"
    assert LLMCall.__name__ == "LLMCall"
    assert LLMClient.__name__ == "LLMClient"
    assert LLMMessage.__name__ == "LLMMessage"
    assert LLMResponse.__name__ == "LLMResponse"
    assert (
        MissingFakeLLMResponseError.__name__
        == "MissingFakeLLMResponseError"
    )
    assert OpenAIChatCompletionClient.__name__ == (
        "OpenAIChatCompletionClient"
    )
    assert OpenAIClientConfigurationError.__name__ == (
        "OpenAIClientConfigurationError"
    )
    assert (
        OpenAIClientDependencyError.__name__
        == "OpenAIClientDependencyError"
    )
    assert OpenAIClientRequestError.__name__ == (
        "OpenAIClientRequestError"
    )
    assert OpenAIClientResponseError.__name__ == (
        "OpenAIClientResponseError"
    )
    assert API_KEY_ENV == "RELEASEGUARD_LLM_API_KEY"
    assert BASE_URL_ENV == "RELEASEGUARD_LLM_BASE_URL"
    assert MODEL_ENV == "RELEASEGUARD_LLM_MODEL"
    assert PROVIDER_ENV == "RELEASEGUARD_LLM_PROVIDER"
    assert TIMEOUT_ENV == "RELEASEGUARD_LLM_TIMEOUT"
    assert LLMProviderConfigurationError.__name__ == (
        "LLMProviderConfigurationError"
    )
    assert LLMRuntime.__name__ == "LLMRuntime"
    assert callable(build_llm_runtime)


def test_llm_public_api_defines_explicit_all() -> None:
    assert llm.__all__ == [
        "LLM_MESSAGE_ROLES",
        "API_KEY_ENV",
        "BASE_URL_ENV",
        "FakeLLMClient",
        "LLMCall",
        "LLMClient",
        "LLMMessage",
        "LLMResponse",
        "LLMProviderConfigurationError",
        "LLMRuntime",
        "MODEL_ENV",
        "MissingFakeLLMResponseError",
        "OpenAIChatCompletionClient",
        "OpenAIClientConfigurationError",
        "OpenAIClientDependencyError",
        "OpenAIClientRequestError",
        "OpenAIClientResponseError",
        "PROVIDER_ENV",
        "TIMEOUT_ENV",
        "build_llm_runtime",
    ]
