"""A non-AI pipeline for exercising the chat lifecycle end to end.

Selected only by ``CHAT_PIPELINE=stub``. Everything it returns is marked so it
cannot be mistaken for a real answer: the text starts with ``STUB_PREFIX``,
``mode`` is ``"STUB"`` (not a legacy mode), and the single source points at a
``stub://`` URL. Token counts are word counts; cost is never estimated.
"""

from __future__ import annotations

from app.chat.pipeline import ChatTurnInput, ChatTurnResult
from app.schemas.chat import SourceItem, TokenUsage

STUB_PREFIX = "[STUB PIPELINE - NOT AN AI ANSWER] "
STUB_MODE = "STUB"
STUB_SOURCE = SourceItem(title="Stub source (no retrieval performed)", url="stub://no-retrieval", type="KB")


def stub_pipeline(turn: ChatTurnInput) -> ChatTurnResult:
    """Echo the question and the continuity inputs the real pipeline would receive."""
    text = f"{STUB_PREFIX}You asked: {turn.question}"
    if turn.current_topic:
        text += f" (topic so far: {turn.current_topic})"
    if turn.memory_summary:
        text += f" (memory: {turn.memory_summary})"
    prompt = len(turn.question.split()) + len(turn.memory_summary.split())
    completion = len(text.split())
    return ChatTurnResult(
        response_text=text,
        token_usage=TokenUsage(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
        ),
        cost_usd=None,
        mode=STUB_MODE,
        topic=turn.question[:80],
        sources=[STUB_SOURCE],
    )
