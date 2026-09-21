"""Clients for external systems. Nothing here knows about HTTP routes or SQL.

- ``azure_openai``   AzureOpenAI client factory, connectivity probe and
                     ``complete_chat`` (answer generation, legacy
                     ChatService._chat). Concept interpretation is not ported.
- ``azure_search``   SearchClient factory, connectivity probe and ``query_kb``
                     (semantic query on the question, legacy kb_search.py
                     without its recency filters / scoring profiles).
- ``probe``          ``python -m app.integrations.probe``: operator check that
                     both services answer; never used by ``/api/chat``
- ``online``         online retrieval: orchestrator, fetchers, trusted-source
                     scoring, Azure AI Foundry grounding (see subpackage)

Each module reads its configuration from ``Settings`` passed in by the caller
and raises ``app.errors.AppError`` subclasses (or lets the SDK error propagate
to the pipeline, which decides whether the answer can proceed without it).
"""
