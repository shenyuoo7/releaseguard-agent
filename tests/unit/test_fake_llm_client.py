import pytest

from releaseguard_agent.llm.client import (
    LLMClient,
    LLMMessage,
    LLMResponse,
)
from releaseguard_agent.llm.fake_client import (
    FakeLLMClient,
    MissingFakeLLMResponseError,
)


def test_fake_llm_client_returns_queued_string_response_and_records_call() -> None:
    client = FakeLLMClient(
        responses=[
            "Release risk is low because all blocking checks passed.",
        ],
    )

    response = client.complete(
        [
            LLMMessage.system("You are ReleaseGuard Agent."),
            LLMMessage.user("Analyze this release."),
        ],
        model="fake-release-risk-model",
        temperature=0.0,
        response_format="json_object",
        metadata={
            "run_id": "run-001",
        },
    )

    assert response == LLMResponse(
        content="Release risk is low because all blocking checks passed.",
        provider="fake",
        model="fake-model",
        finish_reason="stop",
    )

    assert client.queued_response_count == 0
    assert len(client.calls) == 1

    call = client.calls[0]

    assert call.model == "fake-release-risk-model"
    assert call.temperature == 0.0
    assert call.response_format == "json_object"
    assert call.metadata == {
        "run_id": "run-001",
    }
    assert call.messages == (
        LLMMessage.system("You are ReleaseGuard Agent."),
        LLMMessage.user("Analyze this release."),
    )


def test_fake_llm_client_returns_explicit_response_objects() -> None:
    client = FakeLLMClient()

    client.add_response(
        LLMResponse(
            content='{"release_risk": "high"}',
            provider="fake",
            model="fake-json-model",
            finish_reason="stop",
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
            raw={
                "id": "fake-response-001",
            },
        )
    )

    response = client.complete(
        [
            LLMMessage.user("Return structured release risk JSON."),
        ],
    )

    assert response.to_dict() == {
        "content": '{"release_risk": "high"}',
        "provider": "fake",
        "model": "fake-json-model",
        "finish_reason": "stop",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
        },
        "raw": {
            "id": "fake-response-001",
        },
    }


def test_fake_llm_client_records_call_before_missing_response_error() -> None:
    client = FakeLLMClient()

    with pytest.raises(MissingFakeLLMResponseError) as exc_info:
        client.complete(
            [
                LLMMessage.user("Analyze release risk."),
            ],
            metadata={
                "run_id": "run-002",
            },
        )

    assert str(exc_info.value) == (
        "FakeLLMClient has no queued response for this call"
    )
    assert len(client.calls) == 1
    assert client.calls[0].metadata == {
        "run_id": "run-002",
    }


def test_fake_llm_client_copies_mutable_call_inputs() -> None:
    client = FakeLLMClient(
        responses=[
            "The release is blocked by failing tests.",
        ],
    )

    message_metadata = {
        "rule_ids": ["RG-TEST-005"],
    }
    call_metadata = {
        "tags": ["release-risk"],
    }
    message = LLMMessage.user(
        "Analyze failing tests.",
        metadata=message_metadata,
    )

    client.complete(
        [message],
        metadata=call_metadata,
    )

    message_metadata["rule_ids"].append("RG-DEPS-001")
    call_metadata["tags"].append("changed")

    assert client.calls[0].messages[0].metadata == {
        "rule_ids": ["RG-TEST-005"],
    }
    assert client.calls[0].metadata == {
        "tags": ["release-risk"],
    }


def test_llm_message_rejects_unsupported_role() -> None:
    with pytest.raises(ValueError) as exc_info:
        LLMMessage(
            role="critic",
            content="This role is not part of the public contract.",
        )

    assert str(exc_info.value) == "unsupported LLM message role: 'critic'"


def test_llm_message_rejects_empty_content() -> None:
    with pytest.raises(ValueError) as exc_info:
        LLMMessage.user("   ")

    assert str(exc_info.value) == "LLM message content must not be empty"


def test_fake_llm_client_satisfies_llm_client_protocol() -> None:
    client = FakeLLMClient(
        responses=[
            "ok",
        ],
    )

    assert isinstance(client, LLMClient)
