"""Simple conversation memory (plan §3, Phase 2): a raw window plus a compacted summary.

The session keeps every message. The model sees the newest ones raw
(``ChatTurnInput.history``) and, once a conversation is long, a short summary
of everything older, stored in ``chat_sessions.memory_summary`` with
``memory_turns`` = how many messages (oldest first) that summary covers.

Compaction is decided from read data (``services.sessions.MemoryPolicy``),
performed in the pipeline on the internal model (``summarise``), and
persisted in the turn's own write transaction. A failed summarisation is skipped for that turn and
retried naturally next time, because due-ness is recomputed from the counts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.errors import IntegrationError
from app.integrations.azure_openai import ChatCompletion, ChatMessage
from app.schemas.chat import HistoryMessage

logger = logging.getLogger(__name__)

CompleteFn = Callable[[list[ChatMessage]], ChatCompletion]

SUMMARY_MAX_WORDS = 120
SUMMARY_MAX_CHARS = 1500  # hard bound on what is stored, whatever the model returns

SUMMARY_SYSTEM_PROMPT = (
    "You maintain a running summary of a conversation between a user and an assistant about "
    "disaster risk reduction, for an assistant that will continue the conversation.\n\n"
    f"Write the updated summary in at most {SUMMARY_MAX_WORDS} words, in the language of the "
    "conversation. Merge the previous summary (if any) with the new messages. Keep what a "
    "continuing assistant needs: subjects discussed, what the user asked for and cares about, "
    "conclusions reached, open questions. Do not invent facts, do not add advice, do not "
    "mention that this is a summary. Return only the summary text."
)


@dataclass(frozen=True)
class MemoryUpdate:
    """A new summary and the number of messages (oldest first) it now covers."""

    summary: str
    turns: int


def build_summary_messages(
    existing_summary: str, older: list[HistoryMessage], subject: str | None
) -> list[ChatMessage]:
    """System prompt, then previous summary + messages to fold in, as one user message."""
    parts: list[str] = []
    if existing_summary:
        parts.append(f"PREVIOUS SUMMARY:\n{existing_summary}")
    if subject:
        parts.append(f"CURRENT SUBJECT: {subject}")
    lines = "\n".join(f"{m.role}: {m.content}" for m in older)
    parts.append(f"NEW MESSAGES TO FOLD IN:\n{lines}")
    return [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


@dataclass(frozen=True)
class Summarisation:
    """Outcome of one compaction attempt."""

    update: MemoryUpdate | None
    completion: ChatCompletion | None


def summarise(
    complete: CompleteFn,
    existing_summary: str,
    older: list[HistoryMessage],
    subject: str | None,
    covered_so_far: int,
) -> Summarisation:
    """Fold ``older`` into the summary on the internal model; ``update=None`` on failure (logged)."""
    if not older:
        return Summarisation(update=None, completion=None)
    messages = build_summary_messages(existing_summary, older, subject)
    try:
        completion = complete(messages)
    except IntegrationError as exc:
        logger.warning("memory compaction skipped this turn: %s", exc)
        return Summarisation(update=None, completion=None)
    summary = completion.content.strip()[:SUMMARY_MAX_CHARS]
    if not summary:
        logger.warning("memory compaction skipped this turn: empty summary")
        return Summarisation(update=None, completion=completion)
    update = MemoryUpdate(summary=summary, turns=covered_so_far + len(older))
    logger.info(
        "memory compacted: covers %d messages, %d chars, tokens=%d",
        update.turns,
        len(summary),
        completion.total_tokens,
    )
    return Summarisation(update=update, completion=completion)
