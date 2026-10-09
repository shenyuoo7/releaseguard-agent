from pathlib import Path
import pytest

from releaseguard_agent.config.provider import (
    ProviderConfigError,
    get_active_provider,
    load_providers,
    parse_provider_dict,
)


def test_parse_provider_dict_and_env_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELEASEGUARD_TEST_KEY", "sk-secret-key-12345678")
    monkeypatch.setenv("BASE_URL_VAR", "https://api.openai.com/v1")

    data = {
        "name": "my-openai",
        "type": "openai",
        "api_key": "${RELEASEGUARD_TEST_KEY}",
        "model": "gpt-4o",
        "base_url": "$BASE_URL_VAR",
        "thinking": False,
    }

    config = parse_provider_dict(data)
    assert config.name == "my-openai"
    assert config.type == "openai"
    assert config.api_key == "sk-secret-key-12345678"
    assert config.model == "gpt-4o"
    assert config.base_url == "https://api.openai.com/v1"
    assert not config.thinking
    assert config.redacted_api_key() == "sk-...5678"


def test_load_providers_from_temp_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_KEY", "ant-secret-key-999")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
providers:
  - name: primary-claude
    type: anthropic
    api_key: ${ANTHROPIC_KEY}
    model: claude-3-5-sonnet-20241022
    thinking: true
  - name: backup-openai
    type: openai
    api_key: sk-direct-key
    model: gpt-4o-mini
""",
        encoding="utf-8",
    )

    providers = load_providers(config_file)
    assert len(providers) == 2
    assert providers[0].name == "primary-claude"
    assert providers[0].type == "anthropic"
    assert providers[0].thinking is True
    assert providers[0].api_key == "ant-secret-key-999"

    # get_active_provider default (first)
    active = get_active_provider(providers)
    assert active.name == "primary-claude"

    # get_active_provider by name
    backup = get_active_provider(providers, "backup-openai")
    assert backup.name == "backup-openai"


def test_invalid_provider_errors(tmp_path: Path) -> None:
    # Missing model
    with pytest.raises(ProviderConfigError, match="model"):
        parse_provider_dict({"name": "bad", "type": "openai", "api_key": "k"})

    # Unsupported type
    with pytest.raises(ProviderConfigError, match="unsupported type"):
        parse_provider_dict({"name": "bad", "type": "gemini-unknown", "model": "m"})

    # Empty list
    with pytest.raises(ProviderConfigError, match="No LLM providers"):
        get_active_provider([])

    # File not found
    with pytest.raises(ProviderConfigError, match="not found"):
        load_providers(tmp_path / "non_existent.yaml")
