"""Settings are read from the environment exactly once, with canonical names."""

from __future__ import annotations

import pytest

from app.config import load_settings


def test_defaults_without_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABASE_URL", "API_PORT", "LOG_LEVEL", "AZURE_OPENAI_API_KEY", "CORS_ALLOW_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    s = load_settings()
    assert s.database_url is None
    assert s.api_port == 8000
    assert s.log_level == "INFO"
    assert s.cors_allow_origins == ()  # same-origin only unless configured
    assert s.azure_openai_api_key is None


def test_canonical_azure_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
    monkeypatch.setenv("DATABASE_URL", "postgresql://undrr:undrrpassword@postgres:5432/undrr_chat")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://a.example, https://b.example")
    s = load_settings()
    assert s.azure_openai_api_key == "k"
    assert s.database_url is not None and s.database_url.endswith("/undrr_chat")
    assert s.cors_allow_origins == ("https://a.example", "https://b.example")


def test_invalid_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_PORT", "eighty")
    with pytest.raises(ValueError):
        load_settings()


def test_reasoning_settings_are_unset_by_default_and_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CHAT_REASONING_EFFORT", "INTERNAL_LLM_REASONING_EFFORT", "INTERNAL_LLM_JSON_MODE"):
        monkeypatch.delenv(name, raising=False)
    s = load_settings()
    assert s.chat_reasoning_effort is None and s.internal_llm_reasoning_effort is None
    assert s.internal_llm_json_mode is False
    monkeypatch.setenv("CHAT_REASONING_EFFORT", " Low ")
    monkeypatch.setenv("INTERNAL_LLM_REASONING_EFFORT", "minimal")
    monkeypatch.setenv("INTERNAL_LLM_JSON_MODE", "true")
    s = load_settings()
    assert (s.chat_reasoning_effort, s.internal_llm_reasoning_effort, s.internal_llm_json_mode) == (
        "low",
        "minimal",
        True,
    )
