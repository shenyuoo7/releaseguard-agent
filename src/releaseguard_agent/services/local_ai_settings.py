from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from releaseguard_agent.llm import (
    LLMClient,
    LLMMessage,
    LLMRuntime,
    OpenAIChatCompletionClient,
    OpenAIClientRequestError,
)


PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "custom": {
        "label": "OpenAI-compatible custom",
        "base_url": "",
        "model": "",
    },
}


class AiSettingsError(ValueError):
    """Safe, user-facing AI settings error."""


class AiNotConnectedError(AiSettingsError):
    """Raised when a real-model run is requested before a successful test."""


class SecretStore(Protocol):
    def load(self) -> str | None: ...

    def save(self, secret: str) -> None: ...

    def delete(self) -> None: ...


class DpapiFileSecretStore:
    """Store a Windows-user-scoped encrypted secret without plaintext files."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> str | None:
        if not self._path.exists():
            return None
        if os.name != "nt":
            return None
        encrypted = self._path.read_text(encoding="utf-8").strip()
        script = (
            "$value=[Console]::In.ReadToEnd();"
            "$secure=ConvertTo-SecureString $value;"
            "$ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure);"
            "try {[Console]::Out.Write("
            "[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr))} "
            "finally {[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)}"
        )
        return self._run_powershell(script, encrypted)

    def save(self, secret: str) -> None:
        if os.name != "nt":
            raise AiSettingsError("记住此设备仅支持 Windows 本机凭据保护。")
        script = (
            "$value=[Console]::In.ReadToEnd();"
            "$secure=ConvertTo-SecureString $value -AsPlainText -Force;"
            "[Console]::Out.Write((ConvertFrom-SecureString $secure))"
        )
        encrypted = self._run_powershell(script, secret)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(encrypted, encoding="utf-8")

    def delete(self) -> None:
        if self._path.exists():
            self._path.unlink()

    @staticmethod
    def _run_powershell(script: str, stdin: str) -> str:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            input=stdin,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            raise AiSettingsError("Windows 本机凭据保护操作失败。")
        return completed.stdout.strip()


@dataclass(frozen=True)
class ProviderSettings:
    provider: str = "deepseek"
    base_url: str = PROVIDER_PRESETS["deepseek"]["base_url"]
    model: str = PROVIDER_PRESETS["deepseek"]["model"]
    timeout_seconds: float = 60.0
    remember_device: bool = False

    @property
    def provider_label(self) -> str:
        preset = PROVIDER_PRESETS.get(self.provider)
        return preset["label"] if preset else self.provider


@dataclass(frozen=True)
class ConnectionTestResult:
    ok: bool
    status: str
    message: str
    provider: str
    model: str
    response_received: bool
    latency_ms: float
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


ClientBuilder = Callable[..., LLMClient]


class LocalAiSettingsService:
    """Keep non-secret provider settings and securely scoped API credentials."""

    def __init__(
        self,
        runtime_root: Path,
        *,
        secret_store: SecretStore | None = None,
        client_builder: ClientBuilder = OpenAIChatCompletionClient,
    ) -> None:
        self._runtime_root = Path(runtime_root)
        self._config_path = self._runtime_root / "provider.json"
        self._secret_store = secret_store or DpapiFileSecretStore(
            self._runtime_root / "secrets" / "llm_key.dpapi"
        )
        self._client_builder = client_builder
        self._lock = threading.RLock()
        self._settings = self._load_settings()
        self._session_key: str | None = None
        self._tested_signature: str | None = None
        self._connection_status = (
            "untested" if self._available_key() else "unconfigured"
        )
        self._last_error: str | None = None

    @property
    def settings(self) -> ProviderSettings:
        with self._lock:
            return self._settings

    def public_status(self) -> dict[str, object]:
        with self._lock:
            settings = self._settings
            return {
                "status": self._connection_status,
                "configured": self._available_key() is not None,
                "provider": settings.provider,
                "provider_label": settings.provider_label,
                "base_url": settings.base_url,
                "model": settings.model,
                "remember_device": settings.remember_device,
                "last_error": self._last_error,
            }

    def test_connection(
        self,
        settings: ProviderSettings,
        api_key: str,
    ) -> ConnectionTestResult:
        candidate_key = api_key.strip() or self._available_key() or ""
        normalized, key = self._validate(settings, candidate_key)
        started = time.perf_counter()
        try:
            client = self._build_client(normalized, key)
            response = client.complete(
                (LLMMessage.user("Reply with: RELEASEGUARD_CONNECTION_OK"),),
                model=normalized.model,
                temperature=0.0,
                metadata={"purpose": "releaseguard_connection_test"},
            )
        except Exception as exc:
            code, message = _classify_connection_error(exc)
            latency = round((time.perf_counter() - started) * 1000, 1)
            with self._lock:
                self._settings = normalized
                self._session_key = key
                self._tested_signature = None
                self._connection_status = "failed"
                self._last_error = message
            return ConnectionTestResult(
                ok=False,
                status="failed",
                message=message,
                provider=normalized.provider_label,
                model=normalized.model,
                response_received=False,
                latency_ms=latency,
                error_code=code,
            )
        latency = round((time.perf_counter() - started) * 1000, 1)
        with self._lock:
            self._settings = normalized
            self._session_key = key
            self._tested_signature = _signature(normalized, key)
            self._connection_status = "connected"
            self._last_error = None
        return ConnectionTestResult(
            ok=True,
            status="connected",
            message="连接成功，已实际收到模型响应。",
            provider=normalized.provider_label,
            model=response.model or normalized.model,
            response_received=True,
            latency_ms=latency,
        )

    def save(
        self,
        settings: ProviderSettings,
        api_key: str,
    ) -> dict[str, object]:
        key = api_key.strip() or self._available_key()
        normalized, key = self._validate(settings, key or "")
        with self._lock:
            if normalized.remember_device:
                self._secret_store.save(key)
            else:
                self._secret_store.delete()
            self._session_key = key
            remains_tested = self._tested_signature == _signature(normalized, key)
            self._settings = normalized
            self._connection_status = "connected" if remains_tested else "untested"
            self._last_error = None
            self._write_settings(normalized)
        return self.public_status()

    def require_runtime(self) -> LLMRuntime:
        with self._lock:
            key = self._available_key()
            settings = self._settings
            if (
                self._connection_status != "connected"
                or key is None
                or self._tested_signature != _signature(settings, key)
            ):
                raise AiNotConnectedError("请先在“配置 AI”页面测试连接成功。")
            client = self._build_client(settings, key)
            return LLMRuntime(
                mode="llm",
                provider=settings.provider_label,
                model=settings.model,
                client=client,
            )

    def _build_client(self, settings: ProviderSettings, key: str) -> LLMClient:
        return self._client_builder(
            api_key=key,
            model=settings.model,
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            provider_name=settings.provider_label,
            include_metadata=False,
        )

    def _available_key(self) -> str | None:
        if self._session_key:
            return self._session_key
        try:
            return self._secret_store.load()
        except (AiSettingsError, OSError, subprocess.SubprocessError):
            return None

    def _load_settings(self) -> ProviderSettings:
        if not self._config_path.exists():
            return ProviderSettings()
        try:
            payload = json.loads(self._config_path.read_text(encoding="utf-8"))
            return _settings_from_payload(payload)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return ProviderSettings()

    def _write_settings(self, settings: ProviderSettings) -> None:
        self._runtime_root.mkdir(parents=True, exist_ok=True)
        payload = asdict(settings)
        self._config_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _validate(
        settings: ProviderSettings,
        api_key: str,
    ) -> tuple[ProviderSettings, str]:
        provider = settings.provider.strip().lower()
        if provider not in PROVIDER_PRESETS:
            raise AiSettingsError("不支持的 Provider 预设。")
        base_url = settings.base_url.strip().rstrip("/")
        model = settings.model.strip()
        key = api_key.strip()
        if not base_url.startswith(("https://", "http://")):
            raise AiSettingsError("Base URL 必须是有效的 HTTP 或 HTTPS 地址。")
        if not model:
            raise AiSettingsError("Model 不能为空。")
        if not key:
            raise AiSettingsError("API Key 不能为空。")
        if settings.timeout_seconds <= 0:
            raise AiSettingsError("超时时间必须大于 0。")
        return (
            ProviderSettings(
                provider=provider,
                base_url=base_url,
                model=model,
                timeout_seconds=settings.timeout_seconds,
                remember_device=settings.remember_device,
            ),
            key,
        )


def _settings_from_payload(payload: Any) -> ProviderSettings:
    if not isinstance(payload, dict):
        raise ValueError("provider settings must be an object")
    return ProviderSettings(
        provider=str(payload.get("provider", "deepseek")),
        base_url=str(payload.get("base_url", PROVIDER_PRESETS["deepseek"]["base_url"])),
        model=str(payload.get("model", PROVIDER_PRESETS["deepseek"]["model"])),
        timeout_seconds=float(payload.get("timeout_seconds", 60.0)),
        remember_device=bool(payload.get("remember_device", False)),
    )


def _signature(settings: ProviderSettings, api_key: str) -> str:
    value = "\0".join(
        (settings.provider, settings.base_url, settings.model, api_key)
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _classify_connection_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, OpenAIClientRequestError):
        if exc.status_code in {401, 403}:
            return "authentication_failed", "API Key 无效或没有访问权限。"
        if exc.status_code == 404:
            return "model_or_url_not_found", "Base URL 或 Model 不存在。"
        if exc.status_code == 429:
            return "rate_limited", "Provider 返回限流、余额不足或配额受限。"
        lowered = exc.error_type.lower()
        if "timeout" in lowered:
            return "timeout", "连接超时，请检查网络、Base URL 和超时设置。"
        return "provider_error", "Provider 返回异常，请检查配置后重试。"
    if isinstance(exc, TimeoutError):
        return "timeout", "连接超时，请检查网络和 Base URL。"
    if isinstance(exc, AiSettingsError):
        return "invalid_configuration", str(exc)
    return "connection_error", "连接失败，请检查网络、Base URL 和 Model。"
