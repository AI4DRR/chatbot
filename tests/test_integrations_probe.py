"""The operator probe runs both checks, reports per-service, and never touches /api/chat."""

from __future__ import annotations

from typing import Any

import pytest

from app.errors import IntegrationConfigError, IntegrationError
from app.integrations import probe


def test_both_unconfigured_returns_false(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name in (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_CHAT_DEPLOYMENT",
        "AZURE_SEARCH_ENDPOINT",
        "AZURE_SEARCH_KEY",
        "AZURE_SEARCH_INDEX",
    ):
        monkeypatch.delenv(name, raising=False)
    with caplog.at_level("ERROR"):
        assert probe.run_probes() is False
    messages = [r.getMessage() for r in caplog.records]
    assert any("Azure OpenAI: Azure OpenAI not configured" in m for m in messages)
    assert any("Azure AI Search: Azure AI Search not configured" in m for m in messages)


def test_one_failure_still_runs_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    def fake_openai_client(settings: Any) -> object:
        raise IntegrationConfigError("Azure OpenAI not configured: missing AZURE_OPENAI_API_KEY")

    def fake_search_client(settings: Any) -> object:
        return object()

    def fake_probe_index(client: Any, index: str) -> None:
        ran.append(index)

    monkeypatch.setenv("AZURE_SEARCH_INDEX", "idx")
    monkeypatch.setattr(probe, "build_azure_openai_client", fake_openai_client)
    monkeypatch.setattr(probe, "build_search_client", fake_search_client)
    monkeypatch.setattr(probe, "probe_index", fake_probe_index)
    assert probe.run_probes() is False
    assert ran == ["idx"]


def test_success_when_both_probes_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_SEARCH_INDEX", "idx")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "dep")
    monkeypatch.setattr(probe, "build_azure_openai_client", lambda s: object())
    monkeypatch.setattr(probe, "probe_chat_deployment", lambda c, d, v: None)
    monkeypatch.setattr(probe, "build_search_client", lambda s: object())
    monkeypatch.setattr(probe, "probe_index", lambda c, i: None)
    assert probe.run_probes() is True
    assert probe.main() == 0


def test_translated_upstream_error_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_probe(client: Any, deployment: str, version: str) -> None:
        raise IntegrationError("auth", "Azure OpenAI rejected the credentials (HTTP 401)")

    monkeypatch.setenv("AZURE_SEARCH_INDEX", "idx")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "dep")
    monkeypatch.setattr(probe, "build_azure_openai_client", lambda s: object())
    monkeypatch.setattr(probe, "probe_chat_deployment", failing_probe)
    monkeypatch.setattr(probe, "build_search_client", lambda s: object())
    monkeypatch.setattr(probe, "probe_index", lambda c, i: None)
    assert probe.run_probes() is False
