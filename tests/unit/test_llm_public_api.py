import releaseguard_agent.llm as llm
from releaseguard_agent.llm import (
    LLM_MESSAGE_ROLES,
    FakeLLMClient,
    LLMCall,
    LLMClient,
    LLMMessage,
    LLMResponse,
    MissingFakeLLMResponseError,
    OpenAIChatCompletionClient,
    OpenAIClientConfigurationError,
    OpenAIClientDependencyError,
    OpenAIClientRequestError,
    OpenAIClientResponseError,
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


def test_llm_public_api_defines_explicit_all() -> None:
    assert llm.__all__ == [
        "LLM_MESSAGE_ROLES",
        "FakeLLMClient",
        "LLMCall",
        "LLMClient",
        "LLMMessage",
        "LLMResponse",
        "MissingFakeLLMResponseError",
        "OpenAIChatCompletionClient",
        "OpenAIClientConfigurationError",
        "OpenAIClientDependencyError",
        "OpenAIClientRequestError",
        "OpenAIClientResponseError",
    ]
