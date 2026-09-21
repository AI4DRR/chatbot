"""POST /api/chat — one conversation turn.

Two short database transactions with the answer pipeline in between and no
connection open while it runs:

1. read  — validate the session, load its subject/memory, the raw history
   window and (when compaction is due) the older messages to fold in; close.
2. pipeline — internal model (routing, memory), Azure AI Search, Azure
   OpenAI (seconds to tens of seconds).
3. write — question, answer with metadata, session ``updated_at``, and the
   new subject / memory summary when the pipeline produced them; commit.

A pipeline failure therefore persists nothing (the write never starts), and
a write failure rolls back the whole turn.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.api.deps import (
    CurrentUser,
    DbConnector,
    get_app_settings,
    get_chat_pipeline,
    get_db_connector,
    require_owned_session,
)
from app.chat.pipeline import ChatPipeline, ChatTurnInput
from app.config import Settings
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import sessions as session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(
    body: ChatRequest,
    user: CurrentUser,
    connector: DbConnector = Depends(get_db_connector),
    pipeline: ChatPipeline = Depends(get_chat_pipeline),
    settings: Settings = Depends(get_app_settings),
) -> ChatResponse:
    """Submit a message to one of the signed-in user's sessions and return the assistant's turn."""
    policy = session_service.MemoryPolicy(
        window=settings.chat_history_messages,
        compact_after=settings.chat_memory_compact_after,
        compact_every=settings.chat_memory_compact_every,
    )
    with connector() as conn:
        require_owned_session(conn, body.session_id, user)
        context = session_service.get_session_context(conn, body.session_id, policy)

    result = pipeline(
        ChatTurnInput(
            question=body.message,
            memory_summary=context.memory_summary,
            memory_facts=context.memory_facts,
            current_topic=context.current_topic,
            history=context.history,
            subject=context.subject,
            memory_turns=context.memory_turns,
            older_messages=context.older_messages,
        )
    )

    with connector() as conn:
        message_id = session_service.save_turn(
            conn,
            context.session_id,
            body.message,
            result.response_text,
            mode=result.mode,
            topic=result.topic,
            sources=result.sources,
            token_usage=result.token_usage,
            cost_usd=result.cost_usd,
            stats=result.stats,
            details=result.details,
            subject=result.subject,
            memory_summary=result.memory.summary if result.memory else None,
            memory_turns=result.memory.turns if result.memory else None,
        )
    logger.info(
        "chat turn stored for session %s: mode=%s tokens=%d sources=%d",
        context.session_id,
        result.mode,
        result.token_usage.total_tokens,
        len(result.sources),
    )
    return ChatResponse(
        response=result.response_text,
        session_id=context.session_id,
        message_id=message_id,
        token_usage=result.token_usage,
        cost_usd=result.cost_usd,
        mode=result.mode,
        timestamp=datetime.now(UTC),
        sources=result.sources,
    )
