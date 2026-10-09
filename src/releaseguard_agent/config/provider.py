import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml


class ProviderConfigError(Exception):
    """Raised when provider configuration cannot be found or is invalid."""

    pass


@dataclass(frozen=True)
class ProviderConfig:
    """Configuration for an LLM provider endpoint."""

    name: str
    type: Literal["openai", "anthropic"]
    api_key: str
    model: str
    base_url: str | None = None
    thinking: bool = False

    def redacted_api_key(self) -> str:
        """Safely display api key with redaction."""
        if not self.api_key:
            return ""
        if len(self.api_key) <= 8:
            return "***"
        return f"{self.api_key[:3]}...{self.api_key[-4:]}"


def _expand_env_vars(value: str) -> str:
    """Expand environment variables in ${VAR_NAME} or $VAR_NAME format."""
    if not isinstance(value, str):
        return value

    pattern = re.compile(r"\$\{([^}]+)\}|\$([a-zA-Z_][a-zA-Z0-9_]*)")

    def replace_match(match: re.Match[str]) -> str:
        var_name = match.group(1) or match.group(2)
        return os.environ.get(var_name, "")

    return pattern.sub(replace_match, value)


def find_default_config_path(start_dir: Path | None = None) -> Path | None:
    """Find .releaseguard/config.yaml in start_dir, ancestors, or home directory."""
    current = (start_dir or Path.cwd()).resolve()

    # Search current and parent directories
    for directory in [current, *current.parents]:
        candidate = directory / ".releaseguard" / "config.yaml"
        if candidate.is_file():
            return candidate

    # Search user home directory
    home_candidate = Path.home() / ".releaseguard" / "config.yaml"
    if home_candidate.is_file():
        return home_candidate

    return None


def parse_provider_dict(data: dict[str, Any], index: int = 0) -> ProviderConfig:
    """Parse a single provider dictionary into a validated ProviderConfig."""
    name = data.get("name")
    if not name or not isinstance(name, str):
        name = f"provider-{index + 1}"

    ptype = data.get("type", "openai")
    if ptype not in ("openai", "anthropic"):
        raise ProviderConfigError(
            f"Provider '{name}' has unsupported type '{ptype}'. Supported: 'openai', 'anthropic'."
        )

    raw_key = data.get("api_key", "")
    if not isinstance(raw_key, str):
        raise ProviderConfigError(f"Provider '{name}' api_key must be a string.")
    api_key = _expand_env_vars(raw_key).strip()

    model = data.get("model")
    if not model or not isinstance(model, str):
        raise ProviderConfigError(
            f"Provider '{name}' must have a valid non-empty 'model'."
        )

    base_url = data.get("base_url")
    if base_url is not None:
        if not isinstance(base_url, str):
            raise ProviderConfigError(
                f"Provider '{name}' base_url must be a string if provided."
            )
        base_url = _expand_env_vars(base_url).strip() or None

    thinking = bool(data.get("thinking", False))

    return ProviderConfig(
        name=name,
        type=ptype,
        api_key=api_key,
        model=model.strip(),
        base_url=base_url,
        thinking=thinking,
    )


def load_providers(config_path: Path | str | None = None) -> list[ProviderConfig]:
    """Load list of provider configs from yaml file."""
    path: Path | None
    if config_path is not None:
        path = Path(config_path)
        if not path.is_file():
            raise ProviderConfigError(f"Configuration file not found: {path}")
    else:
        path = find_default_config_path()

    if path is None:
        return []

    try:
        content = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
    except Exception as exc:
        raise ProviderConfigError(f"Failed to parse YAML from {path}: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ProviderConfigError(
            f"Invalid config format in {path}: root must be a mapping."
        )

    raw_providers = parsed.get("providers")
    if raw_providers is None:
        return []

    if not isinstance(raw_providers, list):
        raise ProviderConfigError(
            f"Invalid config in {path}: 'providers' must be a list."
        )

    configs: list[ProviderConfig] = []
    for idx, item in enumerate(raw_providers):
        if not isinstance(item, dict):
            raise ProviderConfigError(
                f"Provider item #{idx + 1} in {path} must be a dictionary mapping."
            )
        configs.append(parse_provider_dict(item, idx))

    return configs


def get_active_provider(
    providers: list[ProviderConfig],
    name: str | None = None,
) -> ProviderConfig:
    """Select the active provider by name or default single provider."""
    if not providers:
        raise ProviderConfigError(
            "No LLM providers configured. Please add providers in .releaseguard/config.yaml."
        )

    if name:
        for p in providers:
            if p.name == name:
                return p
        raise ProviderConfigError(f"Provider '{name}' not found in configuration.")

    if len(providers) == 1:
        return providers[0]

    # If multiple providers and no specific name requested, default to the first one
    return providers[0]
