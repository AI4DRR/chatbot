"""User identification, the session lifecycle and turn persistence (legacy ``session_manager.py``).

Functions take an open connection and return schema objects or plain data.
Rules that live here, not in routes or SQL: the default title of a
login-created session, UUID validation of client-supplied ids, the shape of
history/memory, and the metadata layout stored with each message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import DictRow

from app.db import messages as messages_db
from app.db import sessions as sessions_db
from app.db import users as users_db
from app.errors import NotFoundError, ValidationError
from app.schemas.auth import UserResponse
from app.schemas.chat import HistoryMessage, SourceItem, TokenUsage
from app.schemas.sessions import MessageRecord, SessionHistory, SessionInfo

logger = logging.getLogger(__name__)

# Title of every session created by /api/login; the frontends replace it after
# the first turn and render it as "New Chat" until then.
DEFAULT_SESSION_TITLE = "New Chat Sessions"
TITLE_MAX_LENGTH = 200


def parse_session_id(session_id: str) -> UUID:
    """A malformed id can never match a session, so it is a plain not-found."""
    try:
        return UUID(session_id)
    except ValueError:
        raise NotFoundError(f"Session {session_id} not found") from None


_parse_session_id = parse_session_id


def _parse_user_id(user_id: str) -> UUID:
    try:
        return UUID(user_id)
    except ValueError:
        raise ValidationError("user_id must be a UUID") from None


def _user_response(row: DictRow) -> UserResponse:
    return UserResponse(
        id=str(row["id"]),
        email=row["email"],
        name=row["name"],
        department=row["department"],
        unit=row["unit"],
    )


def _session_info(row: DictRow) -> SessionInfo:
    return SessionInfo(
        id=str(row["id"]),
        title=row["title"],
        created_at=row["created_at"],
        memory_turns=row["memory_turns"],
        last_message_at=row["updated_at"],
    )


def require_session(conn: psycopg.Connection[DictRow], session_id: str) -> DictRow:
    """The session row; raises ``NotFoundError`` for unknown and malformed ids."""
    row = sessions_db.get_session(conn, _parse_session_id(session_id))
    if row is None:
        raise NotFoundError(f"Session {session_id} not found")
    return row


def identify_user(
    conn: psycopg.Connection[DictRow],
    email: str,
    name: str | None,
    department: str | None,
    unit: str | None,
) -> UserResponse:
    """Create or enrich the user record without opening a session."""
    return _user_response(users_db.get_or_create_user(conn, email, name, department, unit))


def login(
    conn: psycopg.Connection[DictRow],
    email: str,
    name: str | None,
    department: str | None,
    unit: str | None,
) -> tuple[UserResponse, str]:
    """Identify the user and open a new session; returns ``(user, session_id)``."""
    user = users_db.get_or_create_user(conn, email, name, department, unit)
    session = sessions_db.create_session(conn, user["id"], DEFAULT_SESSION_TITLE)
    return _user_response(user), str(session["id"])


def create_session_for_user(conn: psycopg.Connection[DictRow], user_id: str) -> str:
    """Open a new session for an authenticated user (the ``/api/login`` path); returns its id."""
    session = sessions_db.create_session(conn, _parse_user_id(user_id), DEFAULT_SESSION_TITLE)
    return str(session["id"])


def list_user_sessions(conn: psycopg.Connection[DictRow], user_id: str) -> list[SessionInfo]:
    """Sessions of a user, newest created first. Unknown user → empty list."""
    return [_session_info(r) for r in sessions_db.list_sessions(conn, _parse_user_id(user_id))]


def get_session_info(conn: psycopg.Connection[DictRow], session_id: str) -> SessionInfo:
    """Session summary; raises ``NotFoundError``."""
    return _session_info(require_session(conn, session_id))


def resume_session(conn: psycopg.Connection[DictRow], session_id: str) -> SessionInfo:
    """Validate that a session exists. Legacy behaviour: no state change."""
    return get_session_info(conn, session_id)


def _message_record(row: DictRow) -> MessageRecord:
    metadata: dict[str, Any] = row["metadata"] or {}
    return MessageRecord(
        id=str(row["id"]),
        role=row["role"],
        content=row["content"],
        created_at=row["created_at"],
        token_input=row["token_input"],
        token_output=row["token_output"],
        cost_estimate=row["cost_estimate"],
        sources=[SourceItem.model_validate(s) for s in (metadata.get("sources") or [])],
    )


def get_session_history(conn: psycopg.Connection[DictRow], session_id: str) -> SessionHistory:
    """Session, its messages (oldest first) and its memory; raises ``NotFoundError``."""
    session = require_session(conn, session_id)
    rows = messages_db.get_session_messages(conn, session["id"])
    return SessionHistory(
        session_id=str(session["id"]),
        title=session["title"],
        created_at=session["created_at"],
        messages=[_message_record(r) for r in rows],
        memory_summary=session["memory_summary"],
        memory_facts=session["memory_facts"] or [],
    )


# --- chat turn persistence ----------------------------------------------------


@dataclass(frozen=True)
class SessionContext:
    """What a chat turn starts from: memory, subject, the raw history window, and what to compact."""

    session_id: str
    memory_summary: str = ""
    memory_facts: list[str] = field(default_factory=list)
    current_topic: str | None = None
    history: list[HistoryMessage] = field(default_factory=list)  # oldest first
    subject: str | None = None  # active subject (Phase 2)
    memory_turns: int = 0  # messages the stored summary covers
    total_messages: int = 0
    older_messages: list[HistoryMessage] = field(default_factory=list)  # to compact this turn, if any


@dataclass(frozen=True)
class MemoryPolicy:
    """How much raw history to load and when to compact (from ``Settings``)."""

    window: int = 0  # minimum raw messages (CHAT_HISTORY_MESSAGES); 0 loads no history
    compact_after: int = 0  # only sessions with more messages than this are compacted; 0 disables
    compact_every: int = 0  # ...and only when this many messages are uncovered and outside the window

    def compaction_due(self, total_messages: int, memory_turns: int) -> bool:
        """Long enough, and enough messages sit outside both the summary and the raw window."""
        if self.compact_after <= 0 or self.compact_every <= 0 or total_messages <= self.compact_after:
            return False
        return total_messages - self.window - memory_turns >= self.compact_every


def _to_history(rows: list[DictRow]) -> list[HistoryMessage]:
    return [HistoryMessage(role=r["role"], content=r["content"]) for r in rows]


NO_HISTORY = MemoryPolicy()  # load no raw history and never compact


def get_session_context(
    conn: psycopg.Connection[DictRow], session_id: str, policy: MemoryPolicy = NO_HISTORY
) -> SessionContext:
    """Session memory and subject, the raw history window, and the messages to compact if due.

    The raw window is every message the stored summary does not cover, at
    least ``policy.window`` and at most ``policy.compact_after`` messages, so
    nothing is invisible to the model between compactions. When compaction
    is due (``memory.compaction_due``), the messages older than the window
    are returned in ``older_messages`` for the pipeline to fold into the
    summary, and the window is the newest ``policy.window`` messages.

    ``current_topic`` is the legacy per-message topic; rag never writes it,
    so it is ``None`` in practice and kept only for the stub.
    """
    session = require_session(conn, session_id)
    sid = session["id"]
    last = messages_db.get_last_message(conn, sid)
    topic = None
    if last is not None:
        topic = (last["metadata"] or {}).get("topic")
    memory_turns = int(session["memory_turns"] or 0)
    total = messages_db.count_messages(conn, sid) if policy.window > 0 else 0

    history: list[HistoryMessage] = []
    older: list[HistoryMessage] = []
    if policy.window > 0 and total > 0:
        if policy.compaction_due(total, memory_turns):
            older = _to_history(
                messages_db.get_messages_slice(conn, sid, memory_turns, total - policy.window - memory_turns)
            )
            history = _to_history(messages_db.get_recent_messages(conn, sid, policy.window))
        else:
            uncovered = max(total - memory_turns, policy.window)
            limit = min(uncovered, policy.compact_after) if policy.compact_after > 0 else uncovered
            history = _to_history(messages_db.get_recent_messages(conn, sid, limit))

    return SessionContext(
        session_id=str(sid),
        memory_summary=session["memory_summary"] or "",
        memory_facts=session["memory_facts"] or [],
        current_topic=topic if isinstance(topic, str) and topic else None,
        history=history,
        subject=session["subject"] or None,
        memory_turns=memory_turns,
        total_messages=total,
        older_messages=older,
    )


def save_user_message(conn: psycopg.Connection[DictRow], session_id: str, content: str) -> str:
    """Store the question; returns the message id."""
    row = messages_db.save_message(conn, _parse_session_id(session_id), "user", content, None)
    return str(row["id"])


def save_assistant_message(
    conn: psycopg.Connection[DictRow],
    session_id: str,
    content: str,
    mode: str,
    topic: str | None,
    sources: list[SourceItem],
    token_usage: TokenUsage,
    cost_usd: float | None,
    stats: dict[str, int] | None = None,
    details: dict[str, Any] | None = None,
) -> str:
    """Store the answer with the legacy metadata layout; returns the message id.

    Metadata is ``{"mode", "topic"?, "sources"?, "stats"?, ...details}`` —
    ``topic`` is what the next turn reads back, ``sources`` is what history
    replays, ``stats`` and ``details`` (routing decision, per-call usage) are
    for history and evaluation and are never read back.
    """
    metadata: dict[str, Any] = {"mode": mode}
    if topic:
        metadata["topic"] = topic
    if sources:
        metadata["sources"] = [s.model_dump() for s in sources]
    if stats:
        metadata["stats"] = dict(stats)
    if details:
        metadata.update({k: v for k, v in details.items() if k not in metadata})
    row = messages_db.save_message(
        conn,
        _parse_session_id(session_id),
        "assistant",
        content,
        metadata,
        token_input=token_usage.prompt_tokens,
        token_output=token_usage.completion_tokens,
        cost_estimate=cost_usd,
    )
    return str(row["id"])


def save_turn(
    conn: psycopg.Connection[DictRow],
    session_id: str,
    question: str,
    answer: str,
    mode: str,
    topic: str | None,
    sources: list[SourceItem],
    token_usage: TokenUsage,
    cost_usd: float | None,
    stats: dict[str, int] | None = None,
    details: dict[str, Any] | None = None,
    subject: str | None = None,
    memory_summary: str | None = None,
    memory_turns: int | None = None,
) -> str:
    """Persist one completed turn — one transaction.

    Question, answer, session ``updated_at``, and (Phase 2) the new active
    ``subject`` when the pipeline decided the topic changed and the new
    ``memory_summary``/``memory_turns`` when it compacted; ``None`` leaves
    each unchanged. Called on a fresh connection after the pipeline has
    returned, so no transaction is open while Azure is being waited on, and
    a pipeline failure leaves nothing behind. Returns the assistant message
    id; raises ``NotFoundError`` if the session vanished in between.
    """
    sid = _parse_session_id(session_id)
    if not sessions_db.touch_session(conn, sid):
        raise NotFoundError(f"Session {session_id} not found")
    if subject is not None:
        sessions_db.update_subject(conn, sid, subject)
    if memory_summary is not None and memory_turns is not None:
        sessions_db.update_memory(conn, sid, memory_summary, memory_turns)
    save_user_message(conn, session_id, question)
    return save_assistant_message(
        conn,
        session_id,
        answer,
        mode=mode,
        topic=topic,
        sources=sources,
        token_usage=token_usage,
        cost_usd=cost_usd,
        stats=stats,
        details=details,
    )


def update_session_title(conn: psycopg.Connection[DictRow], session_id: str, title: str) -> str:
    """Store the trimmed, truncated title and return what was stored; raises ``NotFoundError``."""
    stored = title.strip()[:TITLE_MAX_LENGTH]
    if not sessions_db.update_session_title(conn, _parse_session_id(session_id), stored):
        raise NotFoundError(f"Session {session_id} not found")
    return stored
