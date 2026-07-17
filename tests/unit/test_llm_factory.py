import pytest

from releaseguard_agent.llm import (
    LLMProviderConfigurationError,
    build_llm_runtime,
)


def test_default_runtime_is_deterministic_and_builds_no_client() -> None:
    runtime = build_llm_runtime({})

    assert runtime.mode == "deterministic"
    assert runtime.client is None
    assert runtime.enabled is False


def test_missing_key_falls_back_without_constructing_sdk_client() -> None:
    def forbidden_builder(**_kwargs):
        raise AssertionError("client builder must not run without a key")

    runtime = build_llm_runtime(
        {
            "RELEASEGUARD_LLM_PROVIDER": "openai-compatible",
            "RELEASEGUARD_LLM_MODEL": "compatible-chat-model",
        },
        client_builder=forbidden_builder,
    )

    assert runtime.mode == "deterministic"
    assert runtime.fallback_reason == "missing_api_key"
    assert runtime.client is None


def test_compatible_runtime_passes_safe_configuration_to_builder() -> None:
    captured = {}
    fake_client = object()

    def builder(**kwargs):
        captured.update(kwargs)
        return fake_client

    runtime = build_llm_runtime(
        {
            "RELEASEGUARD_LLM_PROVIDER": "openai-compatible",
            "RELEASEGUARD_LLM_MODEL": "deepseek-chat",
            "RELEASEGUARD_LLM_API_KEY": "test-only-key",
            "RELEASEGUARD_LLM_BASE_URL": "https://compatible.invalid/v1",
            "RELEASEGUARD_LLM_TIMEOUT": "12.5",
        },
        client_builder=builder,
    )

    assert runtime.mode == "llm"
    assert runtime.provider == "openai-compatible"
    assert runtime.model == "deepseek-chat"
    assert runtime.client is fake_client
    assert captured == {
        "api_key": "test-only-key",
        "model": "deepseek-chat",
        "base_url": "https://compatible.invalid/v1",
        "timeout": 12.5,
        "provider_name": "openai-compatible",
    }


@pytest.mark.parametrize("timeout", ["zero", "0", "-1"])
def test_invalid_timeout_is_rejected_without_echoing_values(timeout: str) -> None:
    with pytest.raises(
        LLMProviderConfigurationError,
        match="must be a positive number",
    ):
        build_llm_runtime(
            {
                "RELEASEGUARD_LLM_PROVIDER": "openai",
                "RELEASEGUARD_LLM_MODEL": "model",
                "RELEASEGUARD_LLM_API_KEY": "test-only-key",
                "RELEASEGUARD_LLM_TIMEOUT": timeout,
            }
        )


def test_unsupported_provider_error_contains_no_credentials() -> None:
    with pytest.raises(LLMProviderConfigurationError) as captured:
        build_llm_runtime(
            {
                "RELEASEGUARD_LLM_PROVIDER": "unsupported",
                "RELEASEGUARD_LLM_API_KEY": "must-not-appear",
            }
        )

    assert "must-not-appear" not in str(captured.value)
