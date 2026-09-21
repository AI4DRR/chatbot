"""Cost estimate for one turn (legacy ``handle_chat_turn`` step 13)."""

from __future__ import annotations

from app.schemas.chat import TokenUsage


def estimate_cost(usage: TokenUsage, input_per_1k: float, output_per_1k: float) -> float | None:
    """USD for the turn, or ``None`` when neither rate is configured (cost tracking off)."""
    if not (input_per_1k or output_per_1k):
        return None
    return usage.prompt_tokens * input_per_1k / 1000.0 + usage.completion_tokens * output_per_1k / 1000.0
