"""The boundary between the chat lifecycle and the answer pipeline.

The route persists the turn and returns the contract; everything that needs
Azure OpenAI, Azure AI Search or online retrieval sits behind ``ChatPipeline``,
a plain callable from ``ChatTurnInput`` to ``ChatTurnResult``. Three
implementations, chosen by ``CHAT_PIPELINE``:

- ``unconfigured_pipeline`` — the production default until a pipeline is
  chosen. It raises ``ChatPipelineUnavailableError`` (HTTP 503) so
  ``/api/chat`` can never look like it is answering.
- ``app.chat.stub.stub_pipeline`` — an explicit dev/test seam selected only by
  ``CHAT_PIPELINE=stub``; its output is unmistakably not an AI answer.
- ``app.chat.rag.build_rag_pipeline`` — ``CHAT_PIPELINE=rag``: one Azure AI
  Search query, one Azure OpenAI completion, numbered citations (phase 2C).

The pipeline never touches the database: the route reads the session before
calling it and writes the turn after it returns.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.chat.memory import MemoryUpdate
from app.config import Settings
from app.errors import ChatPipelineUnavailableError
from app.schemas.chat import HistoryMessage, SourceItem, TokenUsage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatTurnInput:
    """What the lifecycle knows before the pipeline runs (legacy handle_chat_turn arguments)."""

    question: str
    memory_summary: str = ""
    memory_facts: list[str] = field(default_factory=list)
    current_topic: str | None = None
    history: list[HistoryMessage] = field(default_factory=list)  # recent raw turns, oldest first
    subject: str | None = None  # active subject (chat_sessions.subject), Phase 2
    memory_turns: int = 0  # messages the stored summary covers
    # Messages older than the raw window and not yet covered by the summary,
    # oldest first; non-empty only when the route decided compaction is due.
    older_messages: list[HistoryMessage] = field(default_factory=list)


@dataclass(frozen=True)
class ChatTurnResult:
    """What the pipeline returns (legacy handle_chat_turn return tuple, named)."""

    response_text: str
    token_usage: TokenUsage
    cost_usd: float | None
    mode: str
    topic: str | None
    sources: list[SourceItem] = field(default_factory=list)
    # Pipeline counters (e.g. retrieved/selected/history/cited), stored in the
    # assistant message metadata for evaluation; empty means none recorded.
    stats: dict[str, int] = field(default_factory=dict)
    # Phase 2: what to persist with the turn besides the messages. ``subject``
    # is the new active subject (None = leave unchanged); ``memory`` is the
    # new summary and the count it covers (None = leave unchanged).
    subject: str | None = None
    memory: MemoryUpdate | None = None
    # Free-form details stored in the assistant metadata (routing decision,
    # per-call usage) — for history and evaluation, never read back.
    details: dict[str, Any] = field(default_factory=dict)


ChatPipeline = Callable[[ChatTurnInput], ChatTurnResult]

PIPELINE_UNCONFIGURED = "unconfigured"
PIPELINE_STUB = "stub"
PIPELINE_RAG = "rag"


def unconfigured_pipeline(turn: ChatTurnInput) -> ChatTurnResult:
    """Production default until the AI pipeline is ported: refuse, never answer."""
    raise ChatPipelineUnavailableError()


def select_pipeline(settings: Settings) -> ChatPipeline:
    """The pipeline named by ``CHAT_PIPELINE``; unknown names fail at startup."""
    if settings.chat_pipeline == PIPELINE_UNCONFIGURED:
        return unconfigured_pipeline
    if settings.chat_pipeline == PIPELINE_STUB:
        from app.chat.stub import stub_pipeline  # deferred: never imported in the default configuration

        logger.warning(
            "CHAT_PIPELINE=stub: /api/chat returns placeholder text, not AI answers. Dev/test only."
        )
        return stub_pipeline
    if settings.chat_pipeline == PIPELINE_RAG:
        from app.chat.rag import build_rag_pipeline  # deferred: needs the Azure SDKs configured

        return build_rag_pipeline(settings)
    allowed = f"'{PIPELINE_UNCONFIGURED}', '{PIPELINE_STUB}' or '{PIPELINE_RAG}'"
    raise ValueError(f"CHAT_PIPELINE must be {allowed}, got {settings.chat_pipeline!r}")
