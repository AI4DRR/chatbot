"""Azure AI Search integration: construction, config validation, error translation. No network."""

from __future__ import annotations

from typing import Any

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.search.documents import SearchClient

from app.config import Settings
from app.errors import IntegrationConfigError, IntegrationError
from app.integrations import azure_search
from app.integrations.azure_search import build_search_client, probe_index, translate_search_error

CONFIGURED = Settings(
    azure_search_endpoint="https://example.search.windows.net",
    azure_search_key="not-a-real-key",
    azure_search_index="preventionweb-all",
)


class FakeResults:
    def __init__(self, count: int | None, docs: list[dict[str, Any]]) -> None:
        self._count = count
        self._docs = docs

    def get_count(self) -> int | None:
        return self._count

    def __iter__(self) -> Any:
        return iter(self._docs)


class FakeSearchClient:
    def __init__(self, results: FakeResults | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.results = results if results is not None else FakeResults(1234, [{"title": "t", "content": "c"}])
        self.error = error

    def search(self, **kwargs: Any) -> FakeResults:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.results


def test_client_is_built_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class RecordingSearchClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(azure_search, "SearchClient", RecordingSearchClient)
    build_search_client(CONFIGURED)
    assert captured["endpoint"] == CONFIGURED.azure_search_endpoint
    assert captured["index_name"] == "preventionweb-all"
    assert captured["credential"].key == "not-a-real-key"


def test_real_client_constructs_without_network() -> None:
    assert isinstance(build_search_client(CONFIGURED), SearchClient)


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (Settings(), "AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_KEY, AZURE_SEARCH_INDEX"),
        (Settings(azure_search_endpoint="https://e", azure_search_key="k"), "AZURE_SEARCH_INDEX"),
    ],
)
def test_missing_config_names_variables_only(settings: Settings, expected: str) -> None:
    with pytest.raises(IntegrationConfigError) as info:
        build_search_client(settings)
    assert info.value.detail.endswith(expected)
    assert info.value.status_code == 503


def test_probe_queries_one_document_and_reports_counts_only() -> None:
    client = FakeSearchClient()
    result = probe_index(client, "preventionweb-all")  # type: ignore[arg-type]
    assert client.calls == [{"search_text": "*", "top": 1, "include_total_count": True}]
    assert result.index == "preventionweb-all"
    assert result.document_count == 1234
    assert result.results_returned == 1
    assert not hasattr(result, "documents")


def test_probe_on_empty_index_succeeds() -> None:
    result = probe_index(FakeSearchClient(FakeResults(0, [])), "idx")  # type: ignore[arg-type]
    assert result.document_count == 0
    assert result.results_returned == 0


def _http_error(status: int) -> HttpResponseError:
    exc = HttpResponseError("boom")
    exc.status_code = status
    return exc


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (ClientAuthenticationError("boom"), "auth"),
        (ResourceNotFoundError("boom"), "index"),
        (ServiceRequestError("boom"), "network"),
        (ServiceResponseError("boom"), "network"),
        (_http_error(429), "upstream"),
        (HttpResponseError("boom"), "upstream"),
    ],
)
def test_error_translation(exc: Exception, kind: str) -> None:
    error = translate_search_error(exc)  # type: ignore[arg-type]
    assert error.kind == kind
    assert error.status_code == 502
    assert "boom" not in error.detail


def test_probe_raises_translated_error_with_cause() -> None:
    exc = ResourceNotFoundError("no such index")
    with pytest.raises(IntegrationError) as info:
        probe_index(FakeSearchClient(error=exc), "idx")  # type: ignore[arg-type]
    assert info.value.kind == "index"
    assert info.value.__cause__ is exc


# --- query_kb ------------------------------------------------------------------


def test_query_kb_request_shape_and_document_mapping() -> None:
    client = FakeSearchClient(
        FakeResults(
            None,
            [
                {
                    "title": "T1",
                    "content": "C1",
                    "url": "https://u/1",
                    "release_date": "2025-01-01T00:00:00Z",
                    "@search.score": 4.2,
                    "@search.rerankerScore": 2.9,
                },
                {"title": None, "content": "C2", "url": "https://u/2"},  # sparse row
            ],
        )
    )
    docs = azure_search.query_kb(client, "what is drr?", top_k=5, semantic_config="semantic-config")  # type: ignore[arg-type]
    assert client.calls == [
        {
            "search_text": "what is drr?",
            "top": 5,
            "query_type": "semantic",
            "semantic_configuration_name": "semantic-config",
            "select": ["title", "content", "url", "release_date"],
        }
    ]
    assert docs[0] == azure_search.KbDocument("T1", "C1", "https://u/1", "2025-01-01T00:00:00Z", 4.2, 2.9)
    assert docs[1] == azure_search.KbDocument(None, "C2", "https://u/2", None, None, None)


def test_query_kb_translates_sdk_errors() -> None:
    client = FakeSearchClient(error=ServiceResponseError("timed out"))
    with pytest.raises(IntegrationError) as info:
        azure_search.query_kb(client, "q", 3, "semantic-config")  # type: ignore[arg-type]
    assert info.value.kind == "network" and info.value.status_code == 502


def test_client_timeout_is_passed_to_the_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class RecordingSearchClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(azure_search, "SearchClient", RecordingSearchClient)
    build_search_client(CONFIGURED, timeout=12.5)
    assert (captured["connection_timeout"], captured["read_timeout"]) == (12.5, 12.5)
    captured.clear()
    build_search_client(CONFIGURED)
    assert "read_timeout" not in captured
