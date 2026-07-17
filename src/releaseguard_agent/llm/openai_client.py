from collections.abc import Mapping, Sequence
from typing import Any

from releaseguard_agent.llm.client import LLMMessage, LLMResponse


class OpenAIClientDependencyError(RuntimeError):
    """Raised when the OpenAI SDK is not installed."""


class OpenAIClientConfigurationError(ValueError):
    """Raised when OpenAI client configuration is invalid."""


class OpenAIClientResponseError(RuntimeError):
    """Raised when OpenAI returns an unusable response."""


class OpenAIClientRequestError(RuntimeError):
    """Raised when an OpenAI-compatible SDK request fails."""

    def __init__(
        self,
        *,
        error_type: str,
        status_code: int | None = None,
    ) -> None:
        self.error_type = error_type
        self.status_code = status_code

        message = "OpenAI-compatible request failed."
        if status_code is not None:
            message += f" Status code: {status_code}."

        super().__init__(message)


class OpenAIChatCompletionClient:
    """OpenAI Chat Completions implementation of LLMClient."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        model: str | None = None,
        organization: str | None = None,
        project: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        provider_name: str = "openai",
        include_metadata: bool = True,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            self._client = _build_openai_client(
                api_key=api_key,
                organization=organization,
                project=project,
                base_url=base_url,
                timeout=timeout,
            )
        self._default_model = model
        self._provider_name = provider_name
        self._include_metadata = include_metadata

    def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        response_format: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> LLMResponse:
        """Return one provider-neutral LLM response."""
        selected_model = model or self._default_model
        if selected_model is None:
            raise OpenAIClientConfigurationError(
                "OpenAIChatCompletionClient requires a model."
            )

        request: dict[str, Any] = {
            "model": selected_model,
            "messages": [
                _to_openai_message(message)
                for message in messages
            ],
        }

        if temperature is not None:
            request["temperature"] = temperature

        openai_response_format = _to_openai_response_format(
            response_format
        )
        if openai_response_format is not None:
            request["response_format"] = openai_response_format

        if metadata and self._include_metadata:
            request["metadata"] = {
                str(key): str(value)
                for key, value in dict(metadata).items()
            }

        try:
            completion = self._client.chat.completions.create(**request)
        except Exception as exc:
            raise OpenAIClientRequestError(
                error_type=type(exc).__name__,
                status_code=_extract_status_code(exc),
            ) from exc

        choice = _first_choice(completion)
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)

        if not isinstance(content, str) or not content.strip():
            raise OpenAIClientResponseError(
                "OpenAI response did not contain message content."
            )

        return LLMResponse(
            content=content,
            provider=self._provider_name,
            model=getattr(completion, "model", selected_model),
            finish_reason=getattr(choice, "finish_reason", None),
            usage=_dump_mapping(getattr(completion, "usage", None)),
            raw=_dump_mapping(completion),
        )


def _build_openai_client(
    *,
    api_key: str | None,
    organization: str | None,
    project: str | None,
    base_url: str | None,
    timeout: float | None,
) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise OpenAIClientDependencyError(
            "The OpenAI SDK is required for OpenAIChatCompletionClient. "
            "Install it with `python -m pip install openai`."
        ) from exc

    kwargs: dict[str, Any] = {}
    if api_key is not None:
        kwargs["api_key"] = api_key
    if organization is not None:
        kwargs["organization"] = organization
    if project is not None:
        kwargs["project"] = project
    if base_url is not None:
        kwargs["base_url"] = base_url
    if timeout is not None:
        kwargs["timeout"] = timeout

    return OpenAI(**kwargs)


def _to_openai_message(message: LLMMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": message.role,
        "content": message.content,
    }

    if message.name is not None:
        payload["name"] = message.name

    if message.role == "tool":
        tool_call_id = message.metadata.get("tool_call_id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise OpenAIClientConfigurationError(
                "OpenAI tool messages require metadata['tool_call_id']."
            )
        payload["tool_call_id"] = tool_call_id

    return payload


def _to_openai_response_format(
    response_format: str | None,
) -> dict[str, str] | None:
    if response_format is None:
        return None

    return {
        "type": response_format,
    }


def _first_choice(completion: Any) -> Any:
    choices = getattr(completion, "choices", None)

    if not choices:
        raise OpenAIClientResponseError(
            "OpenAI response did not contain choices."
        )

    return choices[0]


def _dump_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}

    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dict(dumped)

    if isinstance(value, Mapping):
        return dict(value)

    return {}


def _extract_status_code(error: Exception) -> int | None:
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    return None
