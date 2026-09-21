"""The grounded pipeline: route → retrieve → prompt → complete → cite (+ memory).

Phase 2C: one Azure AI Search query (``KB_TOP_K`` candidate chunks, grouped
into at most ``CHAT_KB_MAX_DOCUMENTS`` documents), one Azure OpenAI
completion over the selected excerpts plus recent raw history, ``[n]``
citations resolved to a cited-only source list.

Phase 2 adds, on the *internal* model (``INTERNAL_LLM_*``, by default the
same deployment): a routing call before retrieval that produces the search
query and the active-subject decision (``app.chat.routing``), and — only on
turns the route marked as due — one compaction of older messages into the
session summary (``app.chat.memory``). Both degrade instead of failing the
turn; search and the answer call still fail it.

Phase 3: an answer that cites no excerpt gets a fixed provenance notice
prepended by the pipeline (``prompts.UNSOURCED_NOTICE``) — a fact the
pipeline knows for certain, not a judgement of answer quality.

Nothing else from the legacy ``handle_chat_turn`` — no online retrieval,
intent/entity analysis, LIST mode or facts extraction.

``RagPipeline`` takes its three calls as plain functions so tests can drive
it without the SDKs; ``build_rag_pipeline`` binds them to the real clients.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.chat import citations, context, memory, prompts, routing
from app.chat.cost import estimate_cost
from app.chat.pipeline import ChatTurnInput, ChatTurnResult
from app.config import Settings
from app.integrations import azure_openai, azure_search
from app.integrations.azure_openai import ChatCompletion, ChatMessage
from app.integrations.azure_search import KbDocument
from app.schemas.chat import TokenUsage

logger = logging.getLogger(__name__)

MODE_SYNTHESIS = "SYNTHESIS"

SearchFn = Callable[[str], list[KbDocument]]
CompleteFn = Callable[[list[ChatMessage]], ChatCompletion]


@dataclass(frozen=True)
class RagLimits:
    """Context and history bounds (from ``Settings``)."""

    max_documents: int
    excerpt_chars: int  # per document
    context_chars: int  # all documents together
    history_chars: int = 4000  # per raw history message; the window itself is chosen by the route


def _usage(c: ChatCompletion | None, seconds: float, cost_in: float, cost_out: float) -> dict[str, Any]:
    """Per-call usage record: tokens, wall time and the cost at the configured rates (0 when no call)."""
    if c is None:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "latency_s": 0.0,
            "cost_usd": 0.0,
        }
    usage = TokenUsage(
        prompt_tokens=c.prompt_tokens, completion_tokens=c.completion_tokens, total_tokens=c.total_tokens
    )
    return {
        "prompt_tokens": c.prompt_tokens,
        "completion_tokens": c.completion_tokens,
        "total_tokens": c.total_tokens,
        "latency_s": round(seconds, 2),
        "cost_usd": estimate_cost(usage, cost_in, cost_out) or 0.0,
    }


def _timed(fn: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    return fn(), time.perf_counter() - started


@dataclass(frozen=True)
class RagPipeline:
    """A ``ChatPipeline`` over three injected calls (search, answer model, internal model)."""

    search: SearchFn
    complete: CompleteFn
    internal: CompleteFn
    limits: RagLimits
    cost_input_per_1k: float
    cost_output_per_1k: float

    def __call__(self, turn: ChatTurnInput) -> ChatTurnResult:
        """Answer one turn; raises ``IntegrationError`` if search or the answer call fails."""
        # The route already chose the raw window (every message not covered by the
        # summary, bounded); here only roles are filtered and long messages cut.
        history = context.select_history(turn.history, len(turn.history), self.limits.history_chars)

        # Phase 2: what the conversation is about, before retrieval (degrades, never fails the turn).
        routed, routing_s = _timed(
            lambda: routing.route(self.internal, turn.question, history, turn.subject, turn.memory_summary)
        )
        summarised, memory_s = _timed(
            lambda: memory.summarise(
                self.internal, turn.memory_summary, turn.older_messages, turn.subject, turn.memory_turns
            )
        )
        memory_summary = summarised.update.summary if summarised.update else turn.memory_summary

        retrieved, search_s = _timed(lambda: self.search(routed.result.query))
        documents = context.select_documents(
            retrieved, self.limits.max_documents, self.limits.excerpt_chars, self.limits.context_chars
        )
        messages = prompts.build_messages(documents, history, turn.question, memory_summary)

        completion, answer_s = _timed(lambda: self.complete(messages))

        resolved = citations.resolve_citations(completion.content, documents)
        # Phase 3: an answer that cites nothing is marked as such by the pipeline,
        # deterministically — the model is told not to write its own preamble.
        answer_text = resolved.text if resolved.cited else prompts.with_unsourced_notice(resolved.text)
        rates = (self.cost_input_per_1k, self.cost_output_per_1k)
        per_call = {
            "answer": _usage(completion, answer_s, *rates),
            "routing": _usage(routed.completion, routing_s, *rates),
            "memory": _usage(summarised.completion, memory_s, *rates),
            "search": {"latency_s": round(search_s, 2)},
        }
        llm_calls = (per_call["answer"], per_call["routing"], per_call["memory"])
        usage = TokenUsage(
            prompt_tokens=sum(int(u["prompt_tokens"]) for u in llm_calls),
            completion_tokens=sum(int(u["completion_tokens"]) for u in llm_calls),
            total_tokens=sum(int(u["total_tokens"]) for u in llm_calls),
        )
        stats = {
            "retrieved": len(retrieved),
            "selected": len(documents),
            "history": len(history),
            "cited": len(resolved.cited),
        }
        details: dict[str, Any] = {
            "routing": {
                "query": routed.result.query,
                "topic_changed": routed.result.topic_changed,
                "fallback": routed.fallback,
            },
            "usage": per_call,
        }
        if turn.older_messages:
            details["memory"] = {
                "compacted": summarised.update is not None,
                "turns": summarised.update.turns if summarised.update else turn.memory_turns,
            }
        logger.info(
            "rag turn: retrieved=%d selected=%d history=%d cited=%d tokens=%d rewritten=%s topic_changed=%s",
            stats["retrieved"],
            stats["selected"],
            stats["history"],
            stats["cited"],
            usage.total_tokens,
            routed.result.query != turn.question,
            routed.result.topic_changed,
        )
        return ChatTurnResult(
            response_text=answer_text,
            token_usage=usage,
            cost_usd=estimate_cost(usage, self.cost_input_per_1k, self.cost_output_per_1k),
            mode=MODE_SYNTHESIS,
            topic=None,
            sources=citations.sources_for(resolved.cited),
            stats=stats,
            subject=routed.result.subject if routed.result.topic_changed else None,
            memory=summarised.update,
            details=details,
        )


def build_rag_pipeline(settings: Settings) -> RagPipeline:
    """Bind the pipeline to the configured clients; fails at startup if any is unconfigured."""
    search_client = azure_search.build_search_client(settings, timeout=settings.chat_search_timeout_seconds)
    openai_client = azure_openai.build_azure_openai_client(
        settings, timeout=settings.chat_completion_timeout_seconds
    )
    deployment = azure_openai.chat_deployment(settings)
    internal_client = azure_openai.build_internal_client(settings)
    internal_deployment = azure_openai.internal_deployment(settings)

    def search(question: str) -> list[KbDocument]:
        return azure_search.query_kb(
            search_client, question, settings.kb_top_k, settings.azure_search_semantic_config
        )

    def complete(messages: list[ChatMessage]) -> ChatCompletion:
        return azure_openai.complete_chat(
            openai_client,
            deployment,
            messages,
            settings.chat_max_completion_tokens,
            reasoning_effort=settings.chat_reasoning_effort,
        )

    def internal(messages: list[ChatMessage]) -> ChatCompletion:
        return azure_openai.complete_chat(
            internal_client,
            internal_deployment,
            messages,
            settings.internal_llm_max_completion_tokens,
            reasoning_effort=settings.internal_llm_reasoning_effort,
            json_mode=settings.internal_llm_json_mode,
        )

    logger.info(
        "CHAT_PIPELINE=rag: index=%s deployment=%s internal=%s top_k=%d max_documents=%d history=%d..%d "
        "compact_every=%d max_completion_tokens=%d reasoning=%s/%s json_mode=%s",
        settings.azure_search_index,
        deployment,
        internal_deployment,
        settings.kb_top_k,
        settings.chat_kb_max_documents,
        settings.chat_history_messages,
        settings.chat_memory_compact_after,
        settings.chat_memory_compact_every,
        settings.chat_max_completion_tokens,
        settings.chat_reasoning_effort or "default",
        settings.internal_llm_reasoning_effort or "default",
        settings.internal_llm_json_mode,
    )
    return RagPipeline(
        search=search,
        complete=complete,
        internal=internal,
        limits=RagLimits(
            max_documents=settings.chat_kb_max_documents,
            excerpt_chars=settings.chat_kb_excerpt_chars,
            context_chars=settings.chat_kb_context_chars,
        ),
        cost_input_per_1k=settings.azure_cost_input_per_1k,
        cost_output_per_1k=settings.azure_cost_output_per_1k,
    )
