"""Phase 2 building blocks without Azure: routing contract and parser, memory policy and summariser."""

from __future__ import annotations

import pytest

from app.chat import memory, routing
from app.chat.routing import RoutingResult, parse_routing
from app.errors import IntegrationError
from app.integrations.azure_openai import ChatCompletion
from app.schemas.chat import HistoryMessage
from app.services.sessions import MemoryPolicy


def completion(text: str) -> ChatCompletion:
    return ChatCompletion(
        content=text, finish_reason="stop", prompt_tokens=20, completion_tokens=5, total_tokens=25
    )


# --- routing contract ----------------------------------------------------------


def test_parse_routing_plain_json() -> None:
    r = parse_routing(
        '{"query": "flood early warning financing", "topic_changed": true, "subject": "EW4All"}'
    )
    assert r == RoutingResult(query="flood early warning financing", topic_changed=True, subject="EW4All")


def test_parse_routing_tolerates_fences_and_prose_and_ignores_extra_keys() -> None:
    inner = '{"query": " q ", "topic_changed": false, "subject": "x", "confidence": 0.9}'
    text = f"Here you go:\n```json\n{inner}\n```"
    r = parse_routing(text)
    assert r.query == "q" and r.topic_changed is False
    assert r.subject is None  # subject means nothing unless the topic changed


def test_parse_routing_subject_is_trimmed_and_bounded() -> None:
    r = parse_routing('{"query": "q", "topic_changed": true, "subject": "  ' + "s" * 200 + '  "}')
    assert r.subject == "s" * routing.SUBJECT_MAX_CHARS
    assert parse_routing('{"query": "q", "topic_changed": true, "subject": "   "}').subject is None


@pytest.mark.parametrize(
    "text",
    [
        "no json here",
        '{"query": "q", "topic_changed": "maybe"}',  # bad type
        '{"topic_changed": false}',  # missing query
        '{"query": "   ", "topic_changed": false}',  # blank query
        "[1, 2]",
        '{"query": "q", "topic_changed": false',  # truncated
    ],
)
def test_parse_routing_rejects_unusable_output(text: str) -> None:
    with pytest.raises(ValueError):
        parse_routing(text)


def test_build_routing_messages_structure() -> None:
    msgs = routing.build_routing_messages(
        "How about financing?",
        [
            HistoryMessage(role="user", content="What is EW4All?"),
            HistoryMessage(role="assistant", content="It is…"),
        ],
        "Early warnings",
        "summary text",
    )
    assert msgs[0] == {"role": "system", "content": routing.ROUTING_SYSTEM_PROMPT}
    body = msgs[1]["content"]
    assert body.index("CONVERSATION SO FAR") < body.index("CURRENT SUBJECT: Early warnings")
    assert body.index("RECENT MESSAGES") < body.index("NEW MESSAGE:\nHow about financing?")
    assert "user: What is EW4All?" in body and "assistant: It is…" in body
    first_turn = routing.build_routing_messages("q", [], None, "")[1]["content"]
    assert first_turn == "NEW MESSAGE:\nq"


def test_route_uses_the_model_and_logs_fallback_on_failure() -> None:
    ok = routing.route(
        lambda m: completion('{"query": "x", "topic_changed": true, "subject": "S"}'), "q", [], None, ""
    )
    assert ok.fallback is False and ok.result.query == "x" and ok.result.subject == "S"
    assert ok.completion is not None and ok.completion.total_tokens == 25

    def failing(m: list[dict[str, str]]) -> ChatCompletion:
        raise IntegrationError("network", "down")

    bad = routing.route(failing, "the question", [], "S", "")
    assert bad.fallback is True and bad.completion is None
    assert bad.result == routing.passthrough("the question")
    assert bad.result.topic_changed is False and bad.result.subject is None

    garbage = routing.route(lambda m: completion("nope"), "q", [], None, "")
    assert garbage.fallback is True and garbage.result.query == "q"


# --- memory policy -------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "covered", "due"),
    [
        (12, 0, False),  # not past compact_after
        (13, 0, True),  # 13 - 6 - 0 = 7 >= 6
        (13, 7, False),  # just compacted
        (15, 7, False),  # 15 - 6 - 7 = 2
        (19, 7, True),  # 19 - 6 - 7 = 6
        (40, 0, True),  # compaction failed for a while: still due
    ],
)
def test_memory_policy_due_rule(total: int, covered: int, due: bool) -> None:
    assert MemoryPolicy(window=6, compact_after=12, compact_every=6).compaction_due(total, covered) is due


def test_memory_policy_disabled() -> None:
    assert MemoryPolicy(window=6, compact_after=0, compact_every=6).compaction_due(100, 0) is False
    assert MemoryPolicy(window=6, compact_after=12, compact_every=0).compaction_due(100, 0) is False


# --- summariser ----------------------------------------------------------------


def test_summarise_builds_prompt_and_counts_coverage() -> None:
    seen: list[list[dict[str, str]]] = []

    def internal(m: list[dict[str, str]]) -> ChatCompletion:
        seen.append(m)
        return completion("  Merged summary.  ")

    older = [HistoryMessage(role="user", content="a"), HistoryMessage(role="assistant", content="b")]
    out = memory.summarise(internal, "previous", older, "Subject", covered_so_far=5)
    assert out.update == memory.MemoryUpdate(summary="Merged summary.", turns=7)
    assert seen[0][0]["content"] == memory.SUMMARY_SYSTEM_PROMPT
    body = seen[0][1]["content"]
    assert "PREVIOUS SUMMARY:\nprevious" in body and "CURRENT SUBJECT: Subject" in body
    assert body.endswith("NEW MESSAGES TO FOLD IN:\nuser: a\nassistant: b")


def test_summarise_nothing_to_do_and_failures() -> None:
    assert memory.summarise(lambda m: completion("x"), "", [], None, 0) == memory.Summarisation(None, None)

    def failing(m: list[dict[str, str]]) -> ChatCompletion:
        raise IntegrationError("upstream", "500")

    older = [HistoryMessage(role="user", content="a")]
    assert memory.summarise(failing, "", older, None, 0).update is None
    assert memory.summarise(lambda m: completion("   "), "", older, None, 0).update is None


def test_summarise_bounds_stored_length() -> None:
    out = memory.summarise(
        lambda m: completion("w" * 5000), "", [HistoryMessage(role="user", content="a")], None, 0
    )
    assert out.update is not None and len(out.update.summary) == memory.SUMMARY_MAX_CHARS
