import json
from pathlib import Path
from typing import Any

from releaseguard_agent.llm import (
    LLMMessage,
    LLMResponse,
    OpenAIClientRequestError,
)
from releaseguard_agent.services.local_ai_settings import (
    LocalAiSettingsService,
    ProviderSettings,
)


class MemorySecretStore:
    def __init__(self) -> None:
        self.value: str | None = None

    def load(self) -> str | None:
        return self.value

    def save(self, secret: str) -> None:
        self.value = secret

    def delete(self) -> None:
        self.value = None


class ConnectionClient:
    def __init__(self, response: str = "ok", error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.error:
            raise self.error
        return LLMResponse(content=self.response, provider="fake", model="model")


def _settings(*, remember: bool = False) -> ProviderSettings:
    return ProviderSettings(
        provider="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        remember_device=remember,
    )


def test_session_key_stays_out_of_provider_file_and_public_status(tmp_path: Path) -> None:
    key = "secret-value-that-must-not-leak"
    client = ConnectionClient()
    service = LocalAiSettingsService(
        tmp_path,
        secret_store=MemorySecretStore(),
        client_builder=lambda **kwargs: client,
    )

    tested = service.test_connection(_settings(), key)
    status = service.save(_settings(), "")

    assert tested.ok is True
    assert status["status"] == "connected"
    assert key not in json.dumps(status)
    assert key not in (tmp_path / "provider.json").read_text(encoding="utf-8")
    assert service.require_runtime().enabled is True


def test_session_only_key_does_not_survive_new_service(tmp_path: Path) -> None:
    store = MemorySecretStore()
    service = LocalAiSettingsService(
        tmp_path, secret_store=store, client_builder=lambda **kwargs: ConnectionClient()
    )
    service.test_connection(_settings(), "session-key")
    service.save(_settings(), "")

    restarted = LocalAiSettingsService(
        tmp_path,
        secret_store=MemorySecretStore(),
        client_builder=lambda **kwargs: ConnectionClient(),
    )

    assert restarted.public_status()["status"] == "unconfigured"


def test_remember_device_uses_secret_store_without_plaintext_config(tmp_path: Path) -> None:
    store = MemorySecretStore()
    service = LocalAiSettingsService(
        tmp_path, secret_store=store, client_builder=lambda **kwargs: ConnectionClient()
    )
    settings = _settings(remember=True)
    service.test_connection(settings, "device-key")
    service.save(settings, "")

    assert store.value == "device-key"
    assert "device-key" not in (tmp_path / "provider.json").read_text(encoding="utf-8")
    restarted = LocalAiSettingsService(
        tmp_path, secret_store=store, client_builder=lambda **kwargs: ConnectionClient()
    )
    assert restarted.public_status()["status"] == "untested"
    assert restarted.test_connection(settings, "").ok is True


def test_authentication_and_timeout_errors_are_user_friendly(tmp_path: Path) -> None:
    errors = [
        (
            OpenAIClientRequestError(error_type="AuthenticationError", status_code=401),
            "authentication_failed",
        ),
        (
            OpenAIClientRequestError(error_type="APITimeoutError"),
            "timeout",
        ),
    ]
    for error, expected_code in errors:
        client = ConnectionClient(error=error)
        service = LocalAiSettingsService(
            tmp_path / expected_code,
            secret_store=MemorySecretStore(),
            client_builder=lambda **kwargs: client,
        )
        result = service.test_connection(_settings(), "bad-key")
        assert result.ok is False
        assert result.error_code == expected_code
        assert "Traceback" not in result.message


def test_connection_request_does_not_return_or_embed_api_key(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def builder(**kwargs: Any) -> ConnectionClient:
        captured.update(kwargs)
        return ConnectionClient()

    service = LocalAiSettingsService(
        tmp_path, secret_store=MemorySecretStore(), client_builder=builder
    )
    result = service.test_connection(_settings(), "private-key")

    assert result.response_received is True
    assert "private-key" not in json.dumps(result.to_dict())
    assert captured["include_metadata"] is False
    assert isinstance(LLMMessage.user("test"), LLMMessage)
