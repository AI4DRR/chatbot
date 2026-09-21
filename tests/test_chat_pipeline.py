"""The pipeline boundary: selection, the production default, the stub, cost."""

from __future__ import annotations

import pytest

from app.chat.cost import estimate_cost
from app.chat.pipeline import ChatTurnInput, select_pipeline, unconfigured_pipeline
from app.chat.stub import STUB_MODE, STUB_PREFIX, stub_pipeline
from app.config import Settings, load_settings
from app.errors import ChatPipelineUnavailableError
from app.schemas.chat import TokenUsage


def test_default_settings_select_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAT_PIPELINE", raising=False)
    assert load_settings().chat_pipeline == "unconfigured"
    assert select_pipeline(Settings()) is unconfigured_pipeline


def test_unconfigured_pipeline_refuses() -> None:
    with pytest.raises(ChatPipelineUnavailableError) as exc:
        unconfigured_pipeline(ChatTurnInput(question="hi"))
    assert exc.value.status_code == 503


def test_stub_is_opt_in_and_unmistakable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_PIPELINE", "Stub")
    assert select_pipeline(load_settings()) is stub_pipeline
    result = stub_pipeline(ChatTurnInput(question="What is DRR?", current_topic="drr", memory_summary="s"))
    assert result.response_text.startswith(STUB_PREFIX)
    assert "What is DRR?" in result.response_text and "drr" in result.response_text
    assert result.mode == STUB_MODE and result.mode not in {"SYNTHESIS", "LIST", "REFUSAL"}
    assert result.cost_usd is None
    assert result.sources[0].url.startswith("stub://")
    assert (
        result.token_usage.total_tokens
        == result.token_usage.prompt_tokens + result.token_usage.completion_tokens
    )


def test_unknown_pipeline_name_fails_at_startup() -> None:
    with pytest.raises(ValueError, match="CHAT_PIPELINE"):
        select_pipeline(Settings(chat_pipeline="azure"))


def test_estimate_cost_matches_legacy_formula() -> None:
    usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
    assert estimate_cost(usage, 0.0, 0.0) is None
    assert estimate_cost(usage, 0.0025, 0.01) == pytest.approx(0.0025 + 0.005)
    assert estimate_cost(usage, 0.0, 0.01) == pytest.approx(0.005)
