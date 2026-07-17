import sys
from types import ModuleType

import pytest

from releaseguard_agent.llm import LLMClient, LLMMessage
from releaseguard_agent.llm.openai_client import (
    OpenAIChatCompletionClient,
    OpenAIClientConfigurationError,
    OpenAIClientDependencyError,
    OpenAIClientRequestError,
    OpenAIClientResponseError,
)


class FakeUsage:
    def model_dump(self, *, mode):
        assert mode == "json"

        return {
            "prompt_tokens": 10,
            "completion_tokens": 20,
        }


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content, finish_reason="stop"):
        self.message = FakeMessage(content)
        self.finish_reason = finish_reason


class FakeCompletion:
    model = "gpt-test"

    def __init__(self, content):
        self.choices = [FakeChoice(content)]
        self.usage = FakeUsage()

    def model_dump(self, *, mode):
        assert mode == "json"

        return {
            "id": "chatcmpl-test",
            "model": self.model,
        }


class FakeCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)

        return FakeCompletion('{"ok": true}')


class FakeChat:
    def __init__(self):
        self.completions = FakeCompletions()


class FakeOpenAIClient:
    def __init__(self):
        self.chat = FakeChat()


def test_client_maps_messages_and_returns_llm_response() -> None:
    fake_client = FakeOpenAIClient()
    client = OpenAIChatCompletionClient(
        client=fake_client,
        model="gpt-test",
    )

    response = client.complete(
        [
            LLMMessage.system("System prompt."),
            LLMMessage.user("User prompt."),
        ],
        temperature=0.0,
        response_format="json_object",
        metadata={
            "agent": "ReleaseRiskAnalysisAgent",
        },
    )

    request = fake_client.chat.completions.requests[0]

    assert request["model"] == "gpt-test"
    assert request["temperature"] == 0.0
    assert request["response_format"] == {
        "type": "json_object",
    }
    assert request["metadata"] == {
        "agent": "ReleaseRiskAnalysisAgent",
    }
    assert request["messages"] == [
        {
            "role": "system",
            "content": "System prompt.",
        },
        {
            "role": "user",
            "content": "User prompt.",
        },
    ]

    assert response.content == '{"ok": true}'
    assert response.provider == "openai"
    assert response.model == "gpt-test"
    assert response.finish_reason == "stop"
    assert response.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 20,
    }
    assert response.raw == {
        "id": "chatcmpl-test",
        "model": "gpt-test",
    }
    assert isinstance(client, LLMClient)


def test_client_preserves_explicit_falsy_sdk_client() -> None:
    class FalsyFakeOpenAIClient(FakeOpenAIClient):
        def __bool__(self):
            return False

    fake_client = FalsyFakeOpenAIClient()
    client = OpenAIChatCompletionClient(
        client=fake_client,
        model="gpt-test",
    )

    client.complete([LLMMessage.user("User prompt.")])

    assert len(fake_client.chat.completions.requests) == 1


def test_client_forwards_base_url_and_timeout_to_sdk(monkeypatch) -> None:
    captured_kwargs = {}
    fake_client = FakeOpenAIClient()
    fake_openai_module = ModuleType("openai")

    def build_fake_openai(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_client

    fake_openai_module.OpenAI = build_fake_openai
    monkeypatch.setitem(sys.modules, "openai", fake_openai_module)

    client = OpenAIChatCompletionClient(
        api_key="offline-test-key",
        base_url="https://provider.example/v1",
        timeout=12.5,
        model="provider-model",
    )
    client.complete([LLMMessage.user("User prompt.")])

    assert captured_kwargs == {
        "api_key": "offline-test-key",
        "base_url": "https://provider.example/v1",
        "timeout": 12.5,
    }
    assert fake_client.chat.completions.requests[0]["model"] == (
        "provider-model"
    )


def test_client_reports_missing_sdk_without_import_time_failure(
    monkeypatch,
) -> None:
    monkeypatch.setitem(sys.modules, "openai", None)

    with pytest.raises(OpenAIClientDependencyError) as exc_info:
        OpenAIChatCompletionClient(model="provider-model")

    assert str(exc_info.value) == (
        "The OpenAI SDK is required for OpenAIChatCompletionClient. "
        "Install it with `python -m pip install openai`."
    )


def test_call_model_overrides_default_model() -> None:
    fake_client = FakeOpenAIClient()
    client = OpenAIChatCompletionClient(
        client=fake_client,
        model="default-model",
    )

    client.complete(
        [LLMMessage.user("User prompt.")],
        model="override-model",
    )

    request = fake_client.chat.completions.requests[0]

    assert request["model"] == "override-model"


def test_client_requires_model() -> None:
    client = OpenAIChatCompletionClient(client=FakeOpenAIClient())

    with pytest.raises(OpenAIClientConfigurationError) as exc_info:
        client.complete([LLMMessage.user("User prompt.")])

    assert str(exc_info.value) == (
        "OpenAIChatCompletionClient requires a model."
    )


def test_client_rejects_empty_response_content() -> None:
    class EmptyCompletions(FakeCompletions):
        def create(self, **kwargs):
            self.requests.append(kwargs)

            return FakeCompletion("")

    fake_client = FakeOpenAIClient()
    fake_client.chat.completions = EmptyCompletions()
    client = OpenAIChatCompletionClient(
        client=fake_client,
        model="gpt-test",
    )

    with pytest.raises(OpenAIClientResponseError) as exc_info:
        client.complete([LLMMessage.user("User prompt.")])

    assert str(exc_info.value) == (
        "OpenAI response did not contain message content."
    )


def test_client_rejects_response_without_choices() -> None:
    class NoChoiceCompletion:
        model = "gpt-test"
        choices = []
        usage = None

    class NoChoiceCompletions(FakeCompletions):
        def create(self, **kwargs):
            self.requests.append(kwargs)

            return NoChoiceCompletion()

    fake_client = FakeOpenAIClient()
    fake_client.chat.completions = NoChoiceCompletions()
    client = OpenAIChatCompletionClient(
        client=fake_client,
        model="gpt-test",
    )

    with pytest.raises(OpenAIClientResponseError) as exc_info:
        client.complete([LLMMessage.user("User prompt.")])

    assert str(exc_info.value) == (
        "OpenAI response did not contain choices."
    )


def test_client_converts_sdk_errors_without_exposing_message() -> None:
    class FakeSDKError(RuntimeError):
        status_code = 429

    class FailingCompletions(FakeCompletions):
        def create(self, **kwargs):
            self.requests.append(kwargs)
            raise FakeSDKError("secret-token-should-not-escape")

    fake_client = FakeOpenAIClient()
    fake_client.chat.completions = FailingCompletions()
    client = OpenAIChatCompletionClient(
        client=fake_client,
        model="gpt-test",
    )

    with pytest.raises(OpenAIClientRequestError) as exc_info:
        client.complete([LLMMessage.user("User prompt.")])

    error = exc_info.value
    assert error.error_type == "FakeSDKError"
    assert error.status_code == 429
    assert str(error) == (
        "OpenAI-compatible request failed. Status code: 429."
    )
    assert "secret-token-should-not-escape" not in str(error)


def test_tool_message_requires_tool_call_id() -> None:
    client = OpenAIChatCompletionClient(
        client=FakeOpenAIClient(),
        model="gpt-test",
    )

    with pytest.raises(OpenAIClientConfigurationError) as exc_info:
        client.complete(
            [
                LLMMessage.tool("Tool output."),
            ]
        )

    assert str(exc_info.value) == (
        "OpenAI tool messages require metadata['tool_call_id']."
    )
