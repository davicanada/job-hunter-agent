"""Recency weighting tests — pure Python datetime math (no freezegun).

Seven tests drive ``compute_recency_delta`` directly by injecting an explicit
``now`` so there's no clock-time flakiness. Two clamp tests drive
``score_single_job`` end-to-end with a stubbed LLM response to prove the
``max(0, min(100, llm + delta))`` guard holds at both rails.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest

from src.models.job import Job
from src.scoring.prefilter import PrefilterResult
from src.scoring.scorer import compute_recency_delta, score_single_job
from src.utils.llm_providers import LLMResponse


_NOW = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)


def test_recency_delta_hot():
    posted = _NOW - timedelta(days=1, hours=2)  # age_days = 1
    delta, age_days = compute_recency_delta(posted, now=_NOW)
    assert delta == 3
    assert age_days == 1


def test_recency_delta_normal():
    posted = _NOW - timedelta(days=5, hours=2)  # age_days = 5
    delta, age_days = compute_recency_delta(posted, now=_NOW)
    assert delta == 0
    assert age_days == 5


def test_recency_delta_penalty_8_14():
    posted = _NOW - timedelta(days=10, hours=2)  # age_days = 10
    delta, age_days = compute_recency_delta(posted, now=_NOW)
    assert delta == -5
    assert age_days == 10


def test_recency_delta_penalty_15_30():
    posted = _NOW - timedelta(days=20, hours=2)  # age_days = 20
    delta, age_days = compute_recency_delta(posted, now=_NOW)
    assert delta == -10
    assert age_days == 20


def test_recency_delta_penalty_31_45():
    posted = _NOW - timedelta(days=40, hours=2)  # age_days = 40
    delta, age_days = compute_recency_delta(posted, now=_NOW)
    assert delta == -20
    assert age_days == 40


def test_recency_delta_null():
    delta, age_days = compute_recency_delta(None, now=_NOW)
    assert delta == 0
    assert age_days is None


def test_recency_delta_future(monkeypatch: pytest.MonkeyPatch):
    """A future posted_at is treated as zero-age with a logged warning — this
    usually means either clock skew or a bad feed, not a fresh post."""
    calls: list[tuple[str, dict[str, Any]]] = []

    class _Spy:
        def warning(self, event: str, **kwargs: Any) -> None:
            calls.append((event, kwargs))

        def __getattr__(self, _name: str):  # pragma: no cover - unused levels
            return lambda *a, **k: None

    import src.scoring.scorer as scorer_mod

    monkeypatch.setattr(scorer_mod, "log", _Spy())

    future = _NOW + timedelta(days=1)
    delta, age_days = compute_recency_delta(future, now=_NOW)

    assert delta == 0
    assert age_days == 0
    assert any("future" in event for event, _ in calls)


# ---------------------------------------------------------------------------
# End-to-end clamp tests through score_single_job
# ---------------------------------------------------------------------------
def _stub_chat_with_meta(score: int) -> Any:
    async def _fake(
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        json_mode: bool = False,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        payload = {
            "score": score,
            "verdict": "strong_match",
            "track": "ops_data_analyst",
            "why_match": "Good fit.",
            "watch_out": None,
            "auth_status": "ok_work_permit",
        }
        return LLMResponse(
            content=json.dumps(payload),
            provider="test",
            model="stub",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
        )

    return _fake


def _job(posted_days_ago: int) -> Job:
    return Job(
        id=uuid4(),
        external_id="recency-test",
        source="remoteok",
        title="Data Analyst",
        company="Test Co",
        description="Need SQL + Python.",
        url="https://example.com/job/recency",
        posted_at=_NOW - timedelta(days=posted_days_ago, hours=2),
    )


def _pf() -> PrefilterResult:
    return PrefilterResult(should_score=True)


@pytest.mark.asyncio
async def test_final_score_clamped_high(monkeypatch: pytest.MonkeyPatch):
    """LLM returns 100, recency adds +3 — final score must clamp to 100."""
    import src.scoring.scorer as scorer_mod

    monkeypatch.setattr(scorer_mod, "chat_with_meta", _stub_chat_with_meta(100))
    monkeypatch.setattr(
        scorer_mod,
        "compute_recency_delta",
        lambda posted_at, now=None: (3, 1),
    )

    scored = await score_single_job(_job(1), profile={}, prompt_template="x", prefilter=_pf())
    assert scored is not None
    assert scored.score == 100
    assert scored.recency_bonus == 3
    assert scored.age_days == 1


@pytest.mark.asyncio
async def test_final_score_clamped_low(monkeypatch: pytest.MonkeyPatch):
    """LLM returns 5, recency subtracts 20 — final score must clamp to 0."""
    import src.scoring.scorer as scorer_mod

    monkeypatch.setattr(scorer_mod, "chat_with_meta", _stub_chat_with_meta(5))
    monkeypatch.setattr(
        scorer_mod,
        "compute_recency_delta",
        lambda posted_at, now=None: (-20, 40),
    )

    scored = await score_single_job(_job(40), profile={}, prompt_template="x", prefilter=_pf())
    assert scored is not None
    assert scored.score == 0
    assert scored.recency_bonus == -20
    assert scored.age_days == 40
