"""Semantic query understanding before retrieval (plan §2, Phase 2).

One internal-model call per turn turns the question plus recent context into
a retrieval-ready query and says whether the conversation's active subject
changed. No linguistic rules: the code never inspects the question text.

Contract (``RoutingResult``): ``query`` feeds the search; ``topic_changed``
decides whether ``subject`` is persisted; ``subject`` is null otherwise.

Failure semantics: any error — SDK, timeout, unparsable output — degrades to
exactly the pre-routing behaviour (search on the question as typed, subject
untouched). The turn still answers; the fallback is logged and marked so it
is visible in history and countable by the regression harness.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.errors import IntegrationError
from app.integrations.azure_openai import ChatCompletion, ChatMessage
from app.schemas.chat import HistoryMessage

logger = logging.getLogger(__name__)

CompleteFn = Callable[[list[ChatMessage]], ChatCompletion]

SUBJECT_MAX_CHARS = 80

ROUTING_SYSTEM_PROMPT = (
    "You prepare a user's message for searching a disaster-risk-reduction knowledge base "
    "(UNDRR / PreventionWeb content). You do not answer the message.\n\n"
    "Given the recent conversation, its current subject (if any) and the new message, return "
    "ONLY a JSON object with exactly these keys:\n"
    '  "query": a standalone search query for the new message. If the message continues the '
    "current subject, resolve references (it, that, this, those, the same, how about ...) using "
    "the conversation so the query makes sense on its own. If the message stands on its own, "
    "keep it essentially as written. Keep the language of the message.\n"
    '  "topic_changed": true when the new message is about a meaningfully different subject from '
    "the CURRENT subject (or there is no current subject yet) - including a return to a subject "
    "discussed earlier in the conversation; false only when it continues, narrows or follows up "
    "on the current subject.\n"
    '  "subject": when topic_changed is true, the new subject in at most 8 words; otherwise null.\n\n'
    "No markdown, no explanation, no extra keys."
)

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class RoutingResult(BaseModel):
    """What the internal model decided for this turn."""

    model_config = ConfigDict(extra="ignore")

    query: str = Field(min_length=1)
    topic_changed: bool = False
    subject: str | None = None

    @model_validator(mode="after")
    def _subject_only_on_change(self) -> RoutingResult:
        """``subject`` means nothing unless the topic changed; keep it short and non-blank."""
        subject = (self.subject or "").strip()[:SUBJECT_MAX_CHARS] or None
        self.subject = subject if self.topic_changed else None
        self.query = self.query.strip()
        if not self.query:
            raise ValueError("query is blank")
        return self


@dataclass(frozen=True)
class Routing:
    """The result used for the turn, whether it came from the model or the fallback."""

    result: RoutingResult
    fallback: bool
    completion: ChatCompletion | None  # None when the call failed or was never made


def passthrough(question: str) -> RoutingResult:
    """The pre-routing behaviour: search on the question as typed, subject untouched."""
    return RoutingResult(query=question, topic_changed=False, subject=None)


def build_routing_messages(
    question: str, history: list[HistoryMessage], subject: str | None, memory_summary: str
) -> list[ChatMessage]:
    """System prompt, then the context as one user message ending with the new message."""
    parts: list[str] = []
    if memory_summary:
        parts.append(f"CONVERSATION SO FAR (summary):\n{memory_summary}")
    if subject:
        parts.append(f"CURRENT SUBJECT: {subject}")
    if history:
        lines = "\n".join(f"{m.role}: {m.content}" for m in history)
        parts.append(f"RECENT MESSAGES:\n{lines}")
    parts.append(f"NEW MESSAGE:\n{question}")
    return [
        {"role": "system", "content": ROUTING_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def parse_routing(text: str) -> RoutingResult:
    """Parse the model's JSON (tolerating code fences or prose around it); ``ValueError`` if unusable."""
    match = _JSON_OBJECT.search(text)
    if not match:
        raise ValueError("no JSON object in routing output")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"routing output is not valid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("routing output is not a JSON object")
    try:
        return RoutingResult.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"routing output failed validation: {exc.error_count()} error(s)") from exc


def route(
    complete: CompleteFn,
    question: str,
    history: list[HistoryMessage],
    subject: str | None,
    memory_summary: str,
) -> Routing:
    """Ask the internal model; on any failure fall back to the question as typed."""
    messages = build_routing_messages(question, history, subject, memory_summary)
    try:
        completion = complete(messages)
        result = parse_routing(completion.content)
    except (IntegrationError, ValueError) as exc:
        logger.warning("routing fallback (search on the question as typed): %s", exc)
        return Routing(result=passthrough(question), fallback=True, completion=None)
    logger.info(
        "routing: rewritten=%s topic_changed=%s subject=%r tokens=%d",
        result.query != question,
        result.topic_changed,
        result.subject,
        completion.total_tokens,
    )
    return Routing(result=result, fallback=False, completion=completion)
