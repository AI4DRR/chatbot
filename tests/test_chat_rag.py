"""The RAG pipeline pieces, without Azure: context selection, prompts, citations, orchestration."""

from __future__ import annotations

from typing import Any

import pytest

from app.chat import citations, context, prompts
from app.chat.pipeline import ChatTurnInput, select_pipeline
from app.chat.rag import MODE_SYNTHESIS, RagLimits, RagPipeline, build_rag_pipeline
from app.config import Settings
from app.errors import IntegrationConfigError, IntegrationError
from app.integrations.azure_openai import ChatCompletion
from app.integrations.azure_search import KbDocument
from app.schemas.chat import HistoryMessage


def doc(n: int, **overrides: Any) -> KbDocument:
    base: dict[str, Any] = {
        "title": f"Doc {n}",
        "content": f"Content of document {n}. " * 3,
        "url": f"https://pw.example/{n}",
        "release_date": f"2025-01-0{n}T00:00:00Z",
        "score": 10.0 - n,
        "reranker_score": 3.0 - n / 10,
    }
    base.update(overrides)
    return KbDocument(**base)


# --- context selection ---------------------------------------------------------


def test_select_documents_groups_chunks_by_url_in_ranker_order_and_drops_unusable() -> None:
    docs = [
        doc(1, content=None),  # no text
        doc(2),
        doc(3, url=""),  # nothing to cite
        doc(4, url="https://pw.example/2", content="second chunk of doc 2"),  # same document as doc 2
        doc(5, title=None),  # title falls back to URL
        doc(6),
        doc(7, url="https://pw.example/2", content="third chunk of doc 2"),
    ]
    selected = context.select_documents(
        docs, max_documents=10, max_chars_per_document=10_000, max_total_chars=100_000
    )
    assert [(d.n, d.url, d.chunks) for d in selected] == [
        (1, "https://pw.example/2", 3),
        (2, "https://pw.example/5", 1),
        (3, "https://pw.example/6", 1),
    ]
    assert selected[0].title == "Doc 2"
    assert selected[0].content.split(context.CHUNK_SEPARATOR) == [
        str(doc(2).content).strip(),
        "second chunk of doc 2",
        "third chunk of doc 2",
    ]
    assert selected[1].title == "https://pw.example/5"


def test_select_documents_caps_documents_chunks_per_document_and_chars() -> None:
    same = [doc(i, url="https://pw.example/one", content=f"chunk {i} " * 5) for i in range(1, 6)]
    selected = context.select_documents(same, 10, max_chars_per_document=10_000, max_total_chars=100_000)
    assert len(selected) == 1 and selected[0].chunks == context.CHUNKS_PER_DOCUMENT

    many = [doc(i) for i in range(1, 8)]
    selected = context.select_documents(
        many, max_documents=3, max_chars_per_document=12, max_total_chars=100_000
    )
    assert [d.n for d in selected] == [1, 2, 3]
    assert all(len(d.content) <= 12 for d in selected)
    assert selected[0].content == "Content of d"

    # total budget: the third document gets only what is left, the rest nothing
    selected = context.select_documents(many, max_documents=10, max_chars_per_document=10, max_total_chars=25)
    assert [len(d.content) for d in selected] == [10, 10, 5]


def test_select_history_newest_n_in_order_user_assistant_only() -> None:
    history = [
        HistoryMessage(role="user", content="q1"),
        HistoryMessage(role="assistant", content="a1"),
        HistoryMessage(role="system", content="ignored"),
        HistoryMessage(role="user", content="   "),  # blank is not a turn
        HistoryMessage(role="user", content="q2"),
        HistoryMessage(role="assistant", content="a2" * 50),
    ]
    kept = context.select_history(history, max_messages=3, max_chars=10)
    assert [(m.role, m.content) for m in kept] == [
        ("assistant", "a1"),
        ("user", "q2"),
        ("assistant", "a2a2a2a2a2"),
    ]
    assert context.select_history(history, max_messages=0, max_chars=10) == []


# --- prompt --------------------------------------------------------------------


def test_build_messages_layout_and_numbering() -> None:
    documents = context.select_documents([doc(1), doc(2)], 10, 1000, 10_000)
    history = [
        HistoryMessage(role="user", content="earlier"),
        HistoryMessage(role="assistant", content="reply"),
    ]
    messages = prompts.build_messages(documents, history, "now?")
    assert [m["role"] for m in messages] == ["system", "system", "user", "assistant", "user"]
    assert messages[0]["content"] == prompts.SYSTEM_PROMPT
    excerpts = messages[1]["content"]
    assert "[1] Doc 1 (released 2025-01-01T00:00:00Z)" in excerpts
    assert "[2] Doc 2" in excerpts
    assert "https://" not in excerpts  # URLs are never shown to the model; citations carry them
    assert messages[-1] == {"role": "user", "content": "now?"}


def test_build_messages_without_documents_says_so() -> None:
    messages = prompts.build_messages([], [], "q")
    assert messages[1]["content"] == prompts.NO_EXCERPTS_NOTE


# --- citations -----------------------------------------------------------------


def test_resolve_citations_renumbers_by_first_appearance_and_lists_cited_only() -> None:
    documents = context.select_documents([doc(i) for i in range(1, 6)], 10, 1000, 10_000)
    text = "Floods rise [3]. Warnings help [1][3]. Also [5, 1]."
    resolved = citations.resolve_citations(text, documents)
    assert resolved.text == "Floods rise [1]. Warnings help [2][1]. Also [3][2]."
    assert [d.url for d in resolved.cited] == [
        "https://pw.example/3",
        "https://pw.example/1",
        "https://pw.example/5",
    ]
    sources = citations.sources_for(resolved.cited)
    assert [(s.title, s.url, s.type) for s in sources] == [
        ("Doc 3", "https://pw.example/3", "KB"),
        ("Doc 1", "https://pw.example/1", "KB"),
        ("Doc 5", "https://pw.example/5", "KB"),
    ]


def test_resolve_citations_drops_numbers_that_were_never_offered() -> None:
    documents = context.select_documents([doc(1), doc(2)], 10, 1000, 10_000)
    resolved = citations.resolve_citations("Fact [7]. Other fact [2]. Mixed [9, 2] end [0].", documents)
    assert resolved.text == "Fact. Other fact [1]. Mixed [1] end."
    assert [d.n for d in resolved.cited] == [2]


def test_resolve_citations_without_citations_returns_text_and_no_sources() -> None:
    resolved = citations.resolve_citations("No relevant excerpts; general answer.", [])
    assert resolved.text == "No relevant excerpts; general answer." and resolved.cited == []


# --- orchestration -------------------------------------------------------------


ROUTING_PASS = '{"query": "%s", "topic_changed": false, "subject": null}'


class FakeCalls:
    def __init__(
        self,
        documents: list[KbDocument],
        answer: str = "A [2] B [1].",
        routing: str | None = None,
        summary: str = "summary of older turns",
    ) -> None:
        self.documents = documents
        self.answer = answer
        self.routing = routing  # None → echo the question as the query, no topic change
        self.summary = summary
        self.search_questions: list[str] = []
        self.messages: list[list[dict[str, str]]] = []
        self.internal_messages: list[list[dict[str, str]]] = []
        self.search_error: Exception | None = None
        self.complete_error: Exception | None = None
        self.internal_error: Exception | None = None

    def internal(self, messages: list[dict[str, str]]) -> ChatCompletion:
        self.internal_messages.append(messages)
        if self.internal_error:
            raise self.internal_error
        is_summary = messages[0]["content"].startswith("You maintain a running summary")
        if is_summary:
            content = self.summary
        elif self.routing is not None:
            content = self.routing
        else:
            question = messages[-1]["content"].rsplit("NEW MESSAGE:\n", 1)[-1]
            content = ROUTING_PASS % question.replace('"', '\\"')
        return ChatCompletion(
            content=content, finish_reason="stop", prompt_tokens=30, completion_tokens=10, total_tokens=40
        )

    def search(self, question: str) -> list[KbDocument]:
        self.search_questions.append(question)
        if self.search_error:
            raise self.search_error
        return self.documents

    def complete(self, messages: list[dict[str, str]]) -> ChatCompletion:
        self.messages.append(messages)
        if self.complete_error:
            raise self.complete_error
        return ChatCompletion(
            content=self.answer,
            finish_reason="stop",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )


def make_pipeline(calls: FakeCalls, **limits: Any) -> RagPipeline:
    return RagPipeline(
        search=calls.search,
        complete=calls.complete,
        internal=calls.internal,
        limits=RagLimits(**{"max_documents": 15, "excerpt_chars": 2000, "context_chars": 40_000, **limits}),
        cost_input_per_1k=0.002,
        cost_output_per_1k=0.008,
    )


def test_rag_pipeline_one_search_one_completion_cited_sources_and_cost() -> None:
    calls = FakeCalls([doc(1), doc(2), doc(3)])
    turn = ChatTurnInput(
        question="What is the Sendai Framework?",
        history=[
            HistoryMessage(role="user", content="hi"),
            HistoryMessage(role="assistant", content="hello"),
        ],
        current_topic="ignored by rag",
        memory_summary="ignored by rag",
    )
    result = make_pipeline(calls)(turn)

    # routing echoed the question (no rewrite), so the search text is the question itself
    assert calls.search_questions == ["What is the Sendai Framework?"]
    assert len(calls.messages) == 1 and len(calls.internal_messages) == 1  # one routing call, no memory call
    sent = calls.messages[0]
    assert [m["role"] for m in sent] == ["system", "system", "system", "user", "assistant", "user"]
    assert sent[2]["content"].startswith(prompts.MEMORY_NOTE)  # memory_summary IS used now (Phase 2)
    assert "ignored by rag" not in sent[0]["content"] + sent[1]["content"]  # topic is not used

    assert result.response_text == "A [1] B [2]."
    assert [s.url for s in result.sources] == ["https://pw.example/2", "https://pw.example/1"]
    assert result.mode == MODE_SYNTHESIS and result.topic is None
    assert result.stats == {"retrieved": 3, "selected": 3, "history": 2, "cited": 2}
    # turn totals = answer (100/50) + routing (30/10)
    assert (result.token_usage.prompt_tokens, result.token_usage.completion_tokens) == (130, 60)
    assert result.cost_usd == pytest.approx(130 * 0.002 / 1000 + 60 * 0.008 / 1000)
    assert result.details["usage"]["answer"]["total_tokens"] == 150
    assert result.details["usage"]["routing"]["total_tokens"] == 40
    assert result.details["usage"]["routing"]["cost_usd"] == pytest.approx(
        30 * 0.002 / 1000 + 10 * 0.008 / 1000
    )
    assert (
        result.details["usage"]["answer"]["latency_s"] >= 0
        and "latency_s" in result.details["usage"]["search"]
    )
    assert result.details["usage"]["memory"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "latency_s": 0.0,
        "cost_usd": 0.0,
    }
    assert result.details["routing"] == {
        "query": "What is the Sendai Framework?",
        "topic_changed": False,
        "fallback": False,
    }
    assert result.subject is None and result.memory is None


def test_rag_pipeline_with_no_results_answers_with_the_unsourced_notice() -> None:
    calls = FakeCalls([], answer="In general, early warning systems have four components.")
    result = make_pipeline(calls)(ChatTurnInput(question="q"))
    assert calls.messages[0][1]["content"] == prompts.NO_EXCERPTS_NOTE
    assert result.sources == [] and result.mode == MODE_SYNTHESIS
    assert result.response_text == (
        prompts.UNSOURCED_NOTICE + "\n\nIn general, early warning systems have four components."
    )


def test_rag_pipeline_notice_is_added_by_the_pipeline_not_the_model() -> None:
    """Documents offered but none cited (or only invalid numbers): still the notice, exactly once."""
    calls = FakeCalls([doc(1), doc(2)], answer="Answer citing nothing valid [7].")
    result = make_pipeline(calls)(ChatTurnInput(question="q"))
    assert result.sources == []
    assert result.response_text.startswith(prompts.UNSOURCED_NOTICE + "\n\n")
    assert result.response_text.count(prompts.UNSOURCED_NOTICE) == 1
    assert result.response_text.endswith("Answer citing nothing valid.")


def test_rag_pipeline_cited_answer_has_no_notice() -> None:
    calls = FakeCalls([doc(1)], answer="Grounded [1].")
    result = make_pipeline(calls)(ChatTurnInput(question="q"))
    assert result.sources and prompts.UNSOURCED_NOTICE not in result.response_text


def test_rag_pipeline_search_failure_propagates_before_completion() -> None:
    calls = FakeCalls([doc(1)])
    calls.search_error = IntegrationError("network", "Azure AI Search endpoint unreachable or timed out")
    with pytest.raises(IntegrationError) as exc:
        make_pipeline(calls)(ChatTurnInput(question="q"))
    assert exc.value.kind == "network" and calls.messages == []


def test_rag_pipeline_completion_failure_propagates() -> None:
    calls = FakeCalls([doc(1)])
    calls.complete_error = IntegrationError("upstream", "Azure OpenAI returned HTTP 500")
    with pytest.raises(IntegrationError):
        make_pipeline(calls)(ChatTurnInput(question="q"))


def test_rag_pipeline_applies_limits_and_trusts_the_routes_window() -> None:
    calls = FakeCalls([doc(i) for i in range(1, 10)], answer="x [9]")
    history = [HistoryMessage(role="user", content=f"m{i}" * 3) for i in range(10)]
    result = make_pipeline(calls, max_documents=2, history_chars=4)(
        ChatTurnInput(question="q", history=history)
    )
    sent = calls.messages[0]
    assert "[3]" not in sent[1]["content"]
    assert [m["content"] for m in sent[2:-1]] == [f"m{i}m{i}"[:4] for i in range(10)]  # all 10, each cut
    assert result.sources == [] and result.response_text.endswith(
        "\n\nx"
    )  # [9] was never offered → notice + "x"


# --- Phase 2: routing and memory inside the pipeline ----------------------------


def test_rag_pipeline_searches_the_routed_query_and_returns_the_subject() -> None:
    routed = (
        '{"query": "financing of early warning systems", "topic_changed": true, "subject": "EW4All money"}'
    )
    calls = FakeCalls([doc(1)], routing=routed)
    turn = ChatTurnInput(
        question="How about financing?",
        history=[HistoryMessage(role="user", content="What is EW4All?")],
        subject="Early warnings",
    )
    result = make_pipeline(calls)(turn)
    assert calls.search_questions == ["financing of early warning systems"]
    assert calls.messages[0][-1] == {
        "role": "user",
        "content": "How about financing?",
    }  # model gets the original
    assert result.subject == "EW4All money"
    assert result.details["routing"] == {
        "query": "financing of early warning systems",
        "topic_changed": True,
        "fallback": False,
    }
    routing_prompt = calls.internal_messages[0][1]["content"]
    assert "CURRENT SUBJECT: Early warnings" in routing_prompt and "What is EW4All?" in routing_prompt


def test_rag_pipeline_routing_failure_degrades_to_the_question() -> None:
    calls = FakeCalls([doc(1)])
    calls.internal_error = IntegrationError("network", "Azure OpenAI endpoint unreachable or timed out")
    result = make_pipeline(calls)(ChatTurnInput(question="q", subject="S"))
    assert calls.search_questions == ["q"]
    assert result.details["routing"] == {"query": "q", "topic_changed": False, "fallback": True}
    assert result.subject is None
    assert result.details["usage"]["routing"]["total_tokens"] == 0
    assert result.token_usage.total_tokens == 150  # answer only


def test_rag_pipeline_unparsable_routing_output_degrades() -> None:
    calls = FakeCalls([doc(1)], routing="Sure! Here is the query you asked for.")
    result = make_pipeline(calls)(ChatTurnInput(question="q"))
    assert calls.search_questions == ["q"] and result.details["routing"]["fallback"] is True


def test_rag_pipeline_compacts_older_messages_when_given() -> None:
    calls = FakeCalls([doc(1)], summary="  User asked about floods; assistant explained EW4All.  ")
    older = [HistoryMessage(role="user", content=f"old{i}") for i in range(8)]
    turn = ChatTurnInput(
        question="q",
        history=[HistoryMessage(role="user", content="recent")],
        memory_summary="earlier summary",
        memory_turns=4,
        older_messages=older,
        subject="Floods",
    )
    result = make_pipeline(calls)(turn)
    assert len(calls.internal_messages) == 2  # routing + summary
    summary_prompt = calls.internal_messages[1][1]["content"]
    assert "PREVIOUS SUMMARY:\nearlier summary" in summary_prompt and "old7" in summary_prompt
    assert result.memory is not None
    assert result.memory.summary == "User asked about floods; assistant explained EW4All."
    assert result.memory.turns == 4 + 8
    assert result.details["memory"] == {"compacted": True, "turns": 12}
    # the answer call already sees the NEW summary
    assert calls.messages[0][2]["content"] == prompts.MEMORY_NOTE + result.memory.summary
    assert result.details["usage"]["memory"]["total_tokens"] == 40
    assert result.token_usage.total_tokens == 150 + 40 + 40


def test_rag_pipeline_memory_failure_is_skipped_not_fatal() -> None:
    calls = FakeCalls([doc(1)], summary="   ")  # empty summary counts as a failed compaction
    older = [HistoryMessage(role="user", content="old")]
    result = make_pipeline(calls)(
        ChatTurnInput(question="q", memory_summary="keep me", memory_turns=2, older_messages=older)
    )
    assert result.memory is None
    assert result.details["memory"] == {"compacted": False, "turns": 2}
    assert calls.messages[0][2]["content"] == prompts.MEMORY_NOTE + "keep me"


# --- selection / construction --------------------------------------------------


def test_select_pipeline_rag_requires_azure_configuration() -> None:
    with pytest.raises(IntegrationConfigError, match="AZURE_SEARCH"):
        select_pipeline(Settings(chat_pipeline="rag"))


def test_build_rag_pipeline_binds_settings_without_network() -> None:
    settings = Settings(
        chat_pipeline="rag",
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_api_key="not-a-real-key",
        azure_openai_chat_deployment="gpt-5",
        azure_search_endpoint="https://example.search.windows.net",
        azure_search_key="not-a-real-key",
        azure_search_index="idx",
        kb_top_k=33,
        chat_kb_max_documents=7,
        chat_kb_excerpt_chars=500,
        chat_kb_context_chars=3000,
    )
    pipeline = build_rag_pipeline(settings)
    # candidate count (kb_top_k) is what the index is asked for; max_documents is the separate document cap
    assert pipeline.limits == RagLimits(max_documents=7, excerpt_chars=500, context_chars=3000)
    assert select_pipeline(settings).limits == pipeline.limits  # type: ignore[attr-defined]


def test_build_rag_pipeline_asks_the_index_for_kb_top_k_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The candidate count and the document cap are independent settings (Phase 1, Task 1)."""
    from app.integrations import azure_search as azure_search_module

    asked: list[tuple[int, str]] = []

    def fake_query_kb(client: Any, question: str, top_k: int, semantic_config: str) -> list[KbDocument]:
        asked.append((top_k, semantic_config))
        return [doc(i) for i in range(1, 13)]

    monkeypatch.setattr(azure_search_module, "query_kb", fake_query_kb)
    settings = Settings(
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_api_key="not-a-real-key",
        azure_openai_chat_deployment="gpt-5",
        azure_search_endpoint="https://example.search.windows.net",
        azure_search_key="not-a-real-key",
        azure_search_index="idx",
        azure_search_semantic_config="sem",
        kb_top_k=40,
        chat_kb_max_documents=3,
    )
    pipeline = build_rag_pipeline(settings)
    retrieved = pipeline.search("q")
    assert asked == [(40, "sem")]
    selected = context.select_documents(retrieved, pipeline.limits.max_documents, 6000, 40000)
    assert len(retrieved) == 12 and len(selected) == 3


def test_default_settings_candidates_exceed_document_cap() -> None:
    s = Settings()
    assert s.kb_top_k == 40 and s.chat_kb_max_documents == 8
    assert s.kb_top_k > s.chat_kb_max_documents
