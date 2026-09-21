"""Azure OpenAI: client construction, a connectivity probe and answer generation.

``complete_chat`` is the one production call site (legacy ``ChatService._chat``
request). Concept interpretation (legacy ``concept_interpreter.py``) is not
ported: the phase 2C pipeline makes exactly one completion per turn.

Legacy quirk worth knowing: the old ``api.py`` read ``AZURE_OPENAI_KEY`` and
``AZURE_API_VERSION``, neither of which existed in its ``.env``; it worked only
because the SDK itself falls back to ``AZURE_OPENAI_API_KEY``. This module
passes every value explicitly from ``Settings`` so the SDK's environment
fallbacks never decide what is used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import openai
from openai import AzureOpenAI

from app.config import Settings
from app.errors import IntegrationConfigError, IntegrationError

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 30.0
# Reasoning deployments (gpt-5 family) spend completion budget on reasoning
# before any visible text; a cap this small keeps the probe cheap but may
# leave ``content`` empty. An empty answer with a usage block still proves
# the deployment is callable, which is all the probe is for.
PROBE_MAX_COMPLETION_TOKENS = 128


@dataclass(frozen=True)
class OpenAIProbeResult:
    """Non-sensitive evidence that the chat deployment answered."""

    deployment: str
    api_version: str
    model: str | None
    finish_reason: str | None
    content_received: bool
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


def build_azure_openai_client(settings: Settings, timeout: float = PROBE_TIMEOUT_SECONDS) -> AzureOpenAI:
    """Construct the client from ``Settings`` or fail naming the missing variables."""
    missing = [
        name
        for name, value in (
            ("AZURE_OPENAI_ENDPOINT", settings.azure_openai_endpoint),
            ("AZURE_OPENAI_API_KEY", settings.azure_openai_api_key),
            ("AZURE_OPENAI_API_VERSION", settings.azure_openai_api_version),
        )
        if not value
    ]
    if missing:
        raise IntegrationConfigError(f"Azure OpenAI not configured: missing {', '.join(missing)}")
    assert settings.azure_openai_endpoint is not None  # for mypy; checked above
    return AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        timeout=timeout,
        max_retries=0,
    )


def build_internal_client(settings: Settings, timeout: float | None = None) -> AzureOpenAI:
    """The client for the internal model (routing, memory): ``INTERNAL_LLM_*`` or the answer model's values.

    Every connection value falls back to its ``AZURE_OPENAI_*`` counterpart,
    so with nothing extra configured this is the same deployment as the
    answer model; pointing it elsewhere is a configuration change only.
    """
    endpoint = settings.internal_llm_endpoint or settings.azure_openai_endpoint
    api_key = settings.internal_llm_api_key or settings.azure_openai_api_key
    api_version = settings.internal_llm_api_version or settings.azure_openai_api_version
    missing = [
        name
        for name, value in (
            ("INTERNAL_LLM_ENDPOINT / AZURE_OPENAI_ENDPOINT", endpoint),
            ("INTERNAL_LLM_API_KEY / AZURE_OPENAI_API_KEY", api_key),
            ("INTERNAL_LLM_API_VERSION / AZURE_OPENAI_API_VERSION", api_version),
        )
        if not value
    ]
    if missing:
        raise IntegrationConfigError(f"Internal LLM not configured: missing {', '.join(missing)}")
    assert endpoint is not None  # for mypy; checked above
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
        timeout=settings.internal_llm_timeout_seconds if timeout is None else timeout,
        max_retries=0,
    )


def internal_deployment(settings: Settings) -> str:
    """The internal model's deployment name: ``INTERNAL_LLM_DEPLOYMENT`` or the chat deployment."""
    deployment = settings.internal_llm_deployment or settings.azure_openai_chat_deployment
    if not deployment:
        raise IntegrationConfigError(
            "Internal LLM not configured: missing INTERNAL_LLM_DEPLOYMENT / AZURE_OPENAI_CHAT_DEPLOYMENT"
        )
    return deployment


def chat_deployment(settings: Settings) -> str:
    """The configured chat deployment name, or fail naming the variable."""
    if not settings.azure_openai_chat_deployment:
        raise IntegrationConfigError("Azure OpenAI not configured: missing AZURE_OPENAI_CHAT_DEPLOYMENT")
    return settings.azure_openai_chat_deployment


def translate_openai_error(exc: openai.OpenAIError) -> IntegrationError:
    """Map an SDK exception to an ``IntegrationError`` whose ``kind`` says where to look.

    The provider message is not copied into ``detail``: it can echo request
    content or resource names. Status and error code are enough to classify.
    """
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return IntegrationError("auth", f"Azure OpenAI rejected the credentials (HTTP {exc.status_code})")
    if isinstance(exc, openai.NotFoundError):
        return IntegrationError(
            "deployment", f"Azure OpenAI deployment or route not found (HTTP {exc.status_code})"
        )
    if isinstance(exc, openai.BadRequestError):
        message = str(exc).lower()
        if "api-version" in message or "api version" in message:
            return IntegrationError("api_version", "Azure OpenAI rejected the API version (HTTP 400)")
        return IntegrationError("request", f"Azure OpenAI rejected the request (HTTP {exc.status_code})")
    if isinstance(exc, openai.APIConnectionError):
        return IntegrationError("network", "Azure OpenAI endpoint unreachable or timed out")
    if isinstance(exc, openai.APIStatusError):
        return IntegrationError("upstream", f"Azure OpenAI returned HTTP {exc.status_code}")
    return IntegrationError("upstream", "Azure OpenAI call failed")


def probe_chat_deployment(
    client: AzureOpenAI,
    deployment: str,
    api_version: str,
    max_completion_tokens: int = PROBE_MAX_COMPLETION_TOKENS,
) -> OpenAIProbeResult:
    """One minimal chat completion proving ``deployment`` is callable.

    The request shape is the legacy one (``model`` + ``messages``) plus a
    completion cap; no temperature or other parameters, since the gpt-5
    family rejects several of them.
    """
    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": "Reply with the single word OK."}],
            max_completion_tokens=max_completion_tokens,
        )
    except openai.OpenAIError as exc:
        error = translate_openai_error(exc)
        logger.error("Azure OpenAI probe failed (%s): %s", error.kind, error.detail, exc_info=exc)
        raise error from exc

    choice = response.choices[0] if response.choices else None
    usage = response.usage
    result = OpenAIProbeResult(
        deployment=deployment,
        api_version=api_version,
        model=response.model,
        finish_reason=choice.finish_reason if choice else None,
        content_received=bool(choice and choice.message.content),
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
        total_tokens=usage.total_tokens if usage else None,
    )
    logger.info(
        "Azure OpenAI probe ok: deployment=%s model=%s finish=%s tokens=%s",
        result.deployment,
        result.model,
        result.finish_reason,
        result.total_tokens,
    )
    return result


ChatMessage = dict[str, str]


@dataclass(frozen=True)
class ChatCompletion:
    """The visible answer of one completion plus its usage."""

    content: str
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def complete_chat(
    client: AzureOpenAI,
    deployment: str,
    messages: list[ChatMessage],
    max_completion_tokens: int,
    reasoning_effort: str | None = None,
    json_mode: bool = False,
) -> ChatCompletion:
    """One chat completion with the request shape the probe proved (model, messages, cap).

    No temperature or other sampling parameters: the gpt-5 family rejects
    several of them. ``reasoning_effort`` and ``json_mode`` are sent only
    when asked for (Phase 3 probes showed gpt-5 on Azure accepts both; a
    deployment that rejects them answers HTTP 400, reported as
    ``IntegrationError("request")``). An empty answer is an error, not a
    result — with a reasoning deployment it means the completion cap was
    spent on reasoning.
    """
    extra: dict[str, Any] = {}
    if reasoning_effort:
        extra["reasoning_effort"] = reasoning_effort
    if json_mode:
        extra["response_format"] = {"type": "json_object"}
    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=messages,  # type: ignore[arg-type]  # plain dicts are what the SDK sends
            max_completion_tokens=max_completion_tokens,
            **extra,
        )
    except openai.OpenAIError as exc:
        error = translate_openai_error(exc)
        logger.error("Azure OpenAI completion failed (%s): %s", error.kind, error.detail, exc_info=exc)
        raise error from exc

    choice = response.choices[0] if response.choices else None
    content = (choice.message.content or "").strip() if choice else ""
    finish_reason = choice.finish_reason if choice else None
    usage = response.usage
    result = ChatCompletion(
        content=content,
        finish_reason=finish_reason,
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        total_tokens=usage.total_tokens if usage else 0,
    )
    logger.info(
        "Azure OpenAI completion ok: deployment=%s finish=%s tokens=%d",
        deployment,
        finish_reason,
        result.total_tokens,
    )
    if not content:
        raise IntegrationError(
            "upstream",
            f"Azure OpenAI returned an empty answer (finish_reason={finish_reason}); "
            "with a reasoning deployment raise CHAT_MAX_COMPLETION_TOKENS",
        )
    return result
