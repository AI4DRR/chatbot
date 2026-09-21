"""Azure OpenAI integration: construction, config validation, error translation. No network."""

from __future__ import annotations

from typing import Any

import httpx2 as httpx  # openai 3.x builds its exceptions on httpx2, not httpx
import openai
import pytest

from app.config import Settings
from app.errors import IntegrationConfigError, IntegrationError
from app.integrations import azure_openai
from app.integrations.azure_openai import (
    build_azure_openai_client,
    chat_deployment,
    probe_chat_deployment,
    translate_openai_error,
)

CONFIGURED = Settings(
    azure_openai_endpoint="https://example.openai.azure.com/",
    azure_openai_api_key="not-a-real-key",
    azure_openai_api_version="2025-01-01-preview",
    azure_openai_chat_deployment="gpt-5",
)


def _status_error(
    cls: type[openai.APIStatusError], status: int, message: str = "nope"
) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://example.openai.azure.com/openai/deployments/x/chat/completions")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    return cls(message, response=response, body=None)


class _Usage:
    prompt_tokens = 11
    completion_tokens = 3
    total_tokens = 14


class _Message:
    content = "OK"


class _Choice:
    finish_reason = "stop"
    message = _Message()


class _Response:
    def __init__(self) -> None:
        self.model = "gpt-5-2025-08-07"
        self.choices: list[Any] = [_Choice()]
        self.usage = _Usage()


class FakeCompletions:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = response if response is not None else _Response()
        self.error = error

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = type("Chat", (), {"completions": completions})()


def test_client_is_built_from_settings_not_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class RecordingAzureOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(azure_openai, "AzureOpenAI", RecordingAzureOpenAI)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "env-key-must-not-be-used")
    build_azure_openai_client(CONFIGURED)
    assert captured["azure_endpoint"] == CONFIGURED.azure_openai_endpoint
    assert captured["api_key"] == "not-a-real-key"
    assert captured["api_version"] == "2025-01-01-preview"
    assert captured["max_retries"] == 0


def test_real_client_constructs_without_network() -> None:
    client = build_azure_openai_client(CONFIGURED)
    assert isinstance(client, openai.AzureOpenAI)


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (Settings(), "AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY"),
        (Settings(azure_openai_endpoint="https://e"), "AZURE_OPENAI_API_KEY"),
        (
            Settings(azure_openai_api_key="k", azure_openai_api_version=""),
            "AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION",
        ),
    ],
)
def test_missing_config_names_variables_only(settings: Settings, expected: str) -> None:
    with pytest.raises(IntegrationConfigError) as info:
        build_azure_openai_client(settings)
    assert info.value.detail.endswith(expected)
    assert info.value.status_code == 503
    assert info.value.detail != "k"  # never the value


def test_missing_deployment_is_a_config_error() -> None:
    with pytest.raises(IntegrationConfigError, match="AZURE_OPENAI_CHAT_DEPLOYMENT"):
        chat_deployment(Settings())
    assert chat_deployment(CONFIGURED) == "gpt-5"


def test_probe_sends_minimal_request_and_reports_safe_metadata() -> None:
    completions = FakeCompletions()
    result = probe_chat_deployment(FakeClient(completions), "gpt-5", "2025-01-01-preview")  # type: ignore[arg-type]
    assert completions.calls == [
        {
            "model": "gpt-5",
            "messages": [{"role": "user", "content": "Reply with the single word OK."}],
            "max_completion_tokens": azure_openai.PROBE_MAX_COMPLETION_TOKENS,
        }
    ]
    assert result.deployment == "gpt-5"
    assert result.model == "gpt-5-2025-08-07"
    assert result.finish_reason == "stop"
    assert result.content_received is True
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (11, 3, 14)


def test_probe_with_empty_content_still_succeeds() -> None:
    response = _Response()
    response.choices = [
        type("C", (), {"finish_reason": "length", "message": type("M", (), {"content": None})()})()
    ]
    result = probe_chat_deployment(FakeClient(FakeCompletions(response)), "gpt-5", "v")  # type: ignore[arg-type]
    assert result.content_received is False
    assert result.finish_reason == "length"


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (_status_error(openai.AuthenticationError, 401), "auth"),
        (_status_error(openai.PermissionDeniedError, 403), "auth"),
        (
            _status_error(openai.NotFoundError, 404, "The API deployment for this resource does not exist"),
            "deployment",
        ),
        (_status_error(openai.BadRequestError, 400, "Unsupported api-version"), "api_version"),
        (_status_error(openai.BadRequestError, 400, "Unsupported parameter: temperature"), "request"),
        (_status_error(openai.RateLimitError, 429), "upstream"),
        (_status_error(openai.InternalServerError, 500), "upstream"),
        (openai.APIConnectionError(request=httpx.Request("POST", "https://e")), "network"),
        (openai.APITimeoutError(request=httpx.Request("POST", "https://e")), "network"),
    ],
)
def test_error_translation(exc: openai.OpenAIError, kind: str) -> None:
    error = translate_openai_error(exc)
    assert error.kind == kind
    assert error.status_code == 502
    assert "nope" not in error.detail  # provider message is not echoed


def test_probe_raises_translated_error_with_cause() -> None:
    exc = _status_error(openai.AuthenticationError, 401)
    with pytest.raises(IntegrationError) as info:
        probe_chat_deployment(FakeClient(FakeCompletions(error=exc)), "gpt-5", "v")  # type: ignore[arg-type]
    assert info.value.kind == "auth"
    assert info.value.__cause__ is exc


# --- complete_chat -------------------------------------------------------------


def test_complete_chat_request_shape_and_result() -> None:
    completions = FakeCompletions()
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
    result = azure_openai.complete_chat(FakeClient(completions), "gpt-5", messages, 4000)  # type: ignore[arg-type]
    assert completions.calls == [{"model": "gpt-5", "messages": messages, "max_completion_tokens": 4000}]
    assert result.content == "OK" and result.finish_reason == "stop"
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (11, 3, 14)


def test_complete_chat_empty_answer_is_an_upstream_error() -> None:
    response = _Response()
    response.choices[0].message = type("M", (), {"content": "   "})()
    response.choices[0].finish_reason = "length"
    with pytest.raises(IntegrationError) as info:
        azure_openai.complete_chat(FakeClient(FakeCompletions(response)), "gpt-5", [], 64)  # type: ignore[arg-type]
    assert info.value.kind == "upstream" and "finish_reason=length" in info.value.detail


def test_complete_chat_translates_sdk_errors() -> None:
    completions = FakeCompletions(error=_status_error(openai.RateLimitError, 429))
    with pytest.raises(IntegrationError) as info:
        azure_openai.complete_chat(FakeClient(completions), "gpt-5", [], 64)  # type: ignore[arg-type]
    assert info.value.kind == "upstream" and info.value.status_code == 502


# --- internal model client (Phase 2) ---------------------------------------------


def test_internal_client_defaults_to_the_answer_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class RecordingAzureOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(azure_openai, "AzureOpenAI", RecordingAzureOpenAI)
    azure_openai.build_internal_client(CONFIGURED)
    assert captured["azure_endpoint"] == CONFIGURED.azure_openai_endpoint
    assert captured["api_key"] == "not-a-real-key" and captured["api_version"] == "2025-01-01-preview"
    assert captured["timeout"] == CONFIGURED.internal_llm_timeout_seconds
    assert azure_openai.internal_deployment(CONFIGURED) == "gpt-5"


def test_internal_client_uses_its_own_values_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class RecordingAzureOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(azure_openai, "AzureOpenAI", RecordingAzureOpenAI)
    from dataclasses import replace

    settings = replace(
        CONFIGURED,
        internal_llm_endpoint="https://cheap.openai.azure.com/",
        internal_llm_api_key="other-key",
        internal_llm_api_version="2026-01-01",
        internal_llm_deployment="gpt-5-mini",
        internal_llm_timeout_seconds=12.0,
    )
    azure_openai.build_internal_client(settings)
    assert captured["azure_endpoint"] == "https://cheap.openai.azure.com/"
    assert captured["api_key"] == "other-key" and captured["api_version"] == "2026-01-01"
    assert captured["timeout"] == 12.0
    assert azure_openai.internal_deployment(settings) == "gpt-5-mini"


def test_internal_client_missing_config_names_both_variables() -> None:
    with pytest.raises(IntegrationConfigError) as info:
        azure_openai.build_internal_client(Settings())
    assert "INTERNAL_LLM_ENDPOINT / AZURE_OPENAI_ENDPOINT" in info.value.detail
    with pytest.raises(IntegrationConfigError, match="INTERNAL_LLM_DEPLOYMENT"):
        azure_openai.internal_deployment(Settings())


def test_complete_chat_sends_optional_parameters_only_when_set() -> None:
    completions = FakeCompletions()
    azure_openai.complete_chat(FakeClient(completions), "gpt-5", [], 64)  # type: ignore[arg-type]
    assert set(completions.calls[0]) == {"model", "messages", "max_completion_tokens"}
    client: Any = FakeClient(completions)
    azure_openai.complete_chat(client, "gpt-5", [], 64, reasoning_effort="minimal", json_mode=True)
    assert completions.calls[1]["reasoning_effort"] == "minimal"
    assert completions.calls[1]["response_format"] == {"type": "json_object"}
