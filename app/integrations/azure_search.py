"""Azure AI Search: client construction, a connectivity probe and the KB query.

``query_kb`` is the minimal port of legacy ``kb_search.query_kb``: one semantic
query on the current question. The legacy recency/news/event filters, scoring
profiles and date re-sort are deliberately not ported (phase 2C scope).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import (
    AzureError,
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.search.documents import SearchClient

from app.config import Settings
from app.errors import IntegrationConfigError, IntegrationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KbDocument:
    """One retrieved knowledge-base chunk (the fields the legacy query selected)."""

    title: str | None
    content: str | None
    url: str | None
    release_date: str | None
    score: float | None
    reranker_score: float | None


@dataclass(frozen=True)
class SearchProbeResult:
    """Non-sensitive evidence that the index answered a query."""

    index: str
    document_count: int | None
    results_returned: int


def build_search_client(settings: Settings, timeout: float | None = None) -> SearchClient:
    """Construct the client for the configured index or fail naming the missing variables.

    ``timeout`` (seconds) bounds both connecting and reading for every call
    made through the client; ``None`` keeps the SDK defaults (the probe).
    """
    missing = [
        name
        for name, value in (
            ("AZURE_SEARCH_ENDPOINT", settings.azure_search_endpoint),
            ("AZURE_SEARCH_KEY", settings.azure_search_key),
            ("AZURE_SEARCH_INDEX", settings.azure_search_index),
        )
        if not value
    ]
    if missing:
        raise IntegrationConfigError(f"Azure AI Search not configured: missing {', '.join(missing)}")
    assert settings.azure_search_endpoint and settings.azure_search_key and settings.azure_search_index
    transport_options = {"connection_timeout": timeout, "read_timeout": timeout} if timeout else {}
    return SearchClient(
        endpoint=settings.azure_search_endpoint,
        index_name=settings.azure_search_index,
        credential=AzureKeyCredential(settings.azure_search_key),
        **transport_options,
    )


def translate_search_error(exc: AzureError) -> IntegrationError:
    """Map an Azure SDK exception to an ``IntegrationError`` classified by cause."""
    if isinstance(exc, ClientAuthenticationError):
        return IntegrationError("auth", "Azure AI Search rejected the credentials (HTTP 401/403)")
    if isinstance(exc, ResourceNotFoundError):
        return IntegrationError("index", "Azure AI Search index not found (HTTP 404)")
    if isinstance(exc, ServiceRequestError | ServiceResponseError):
        return IntegrationError("network", "Azure AI Search endpoint unreachable or timed out")
    if isinstance(exc, HttpResponseError):
        status = exc.status_code if exc.status_code is not None else "?"
        return IntegrationError("upstream", f"Azure AI Search returned HTTP {status}")
    return IntegrationError("upstream", "Azure AI Search call failed")


def probe_index(client: SearchClient, index: str) -> SearchProbeResult:
    """One minimal query (``*``, top 1) proving the index is queryable.

    Document contents are consumed only to count them; nothing from the index
    is retained or logged.
    """
    try:
        results = client.search(search_text="*", top=1, include_total_count=True)
        # Iterate before ``get_count()``: the SDK resets its continuation token
        # in ``get_count``, so the other order fetches the first page twice.
        returned = sum(1 for _ in results)
        total = results.get_count()
    except AzureError as exc:
        error = translate_search_error(exc)
        logger.error("Azure AI Search probe failed (%s): %s", error.kind, error.detail, exc_info=exc)
        raise error from exc

    result = SearchProbeResult(index=index, document_count=total, results_returned=returned)
    logger.info(
        "Azure AI Search probe ok: index=%s documents=%s returned=%s",
        result.index,
        result.document_count,
        result.results_returned,
    )
    return result


def query_kb(client: SearchClient, question: str, top_k: int, semantic_config: str) -> list[KbDocument]:
    """Semantic search on ``question``; up to ``top_k`` chunks in the ranker's order.

    Only the selected fields are read. Nothing from the index is logged except
    the number of results.
    """
    try:
        results = client.search(
            search_text=question,
            top=top_k,
            query_type="semantic",
            semantic_configuration_name=semantic_config,
            select=["title", "content", "url", "release_date"],
        )
        documents = [
            KbDocument(
                title=r.get("title"),
                content=r.get("content"),
                url=r.get("url"),
                release_date=r.get("release_date"),
                score=r.get("@search.score"),
                reranker_score=r.get("@search.rerankerScore"),
            )
            for r in results
        ]
    except AzureError as exc:
        error = translate_search_error(exc)
        logger.error("Azure AI Search query failed (%s): %s", error.kind, error.detail, exc_info=exc)
        raise error from exc
    logger.info("Azure AI Search query ok: results=%d top_k=%d", len(documents), top_k)
    return documents
