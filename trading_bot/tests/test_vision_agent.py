"""VisionAgent: per-ticker TTL caching, and the code-based structure fallback.

The structure read (_structure_score) is what runs whenever the LLM path
isn't taken — llm_enabled=False (the default), no key, no chart image, or
the LLM call fails — so it must always produce a real signal, never a bare
neutral pass, once there's enough bar history to read.
"""
import asyncio

import numpy as np
import pytest

from agents.vision_agent import VisionAgent
from core.models import AnalysisContext
from conftest import make_session_bars


def _agent(tmp_path, *, ttl=60.0, response='{"score": 80, "pattern": "p", "reason": "r"}'):
    """VisionAgent with a stubbed vision call that counts invocations."""
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"\x89PNG fake-bytes")
    agent = VisionAgent(gemini_api_key="dummy-key", cache_ttl_min=ttl)
    calls = {"n": 0}

    async def fake_vision(_raw, _prompt, _media):
        calls["n"] += 1
        return response

    agent._llm.vision = fake_vision          # stub the network call
    return agent, calls, str(chart)


def _ctx(ticker, chart):
    return AnalysisContext(ticker=ticker, chart_image_path=chart)


def test_repeat_scan_uses_cache(tmp_path):
    agent, calls, chart = _agent(tmp_path)
    e1 = asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    e2 = asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    assert calls["n"] == 1                    # second scan served from cache
    assert e1.score == e2.score == 80
    assert e2.confidence == 0.7               # real read, not a neutral fallback


def test_ttl_zero_disables_cache(tmp_path):
    agent, calls, chart = _agent(tmp_path, ttl=0)
    asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    assert calls["n"] == 2                     # every scan calls the model


def test_cache_is_per_ticker(tmp_path):
    agent, calls, chart = _agent(tmp_path)
    asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    asyncio.run(agent.evaluate(_ctx("AAPL", chart)))
    assert calls["n"] == 2                     # different tickers, separate reads


def test_expired_entry_refetches(tmp_path):
    agent, calls, chart = _agent(tmp_path)
    asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    # Force the cached entry to look stale.
    ts, ev = agent._cache["NVDA"]
    agent._cache["NVDA"] = (ts - agent._cache_ttl - 1, ev)
    asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    assert calls["n"] == 2


def test_failures_are_not_cached(tmp_path):
    # Empty response -> falls back to the code-based structure read (neutral
    # here since _ctx() sets no bars); must retry the LLM next scan, not cache
    # the fallback.
    agent, calls, chart = _agent(tmp_path, response="")
    e1 = asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    e2 = asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    assert calls["n"] == 2
    assert e1.score == e2.score == 50.0        # neutral, not cached


# ── code-based structure read (no LLM) ───────────────────────────────────────
# What runs whenever the LLM path isn't taken: llm_enabled=False (the default),
# no key, no image, or an LLM failure. Must be a real signal, not a pass.

def _zigzag_closes(base: float, peaks: list[float], troughs: list[float], leg: int = 6) -> list[float]:
    """Alternating up/down legs (peak, trough, peak, trough, ...) from base."""
    closes = [base]
    level = base
    for p, t in zip(peaks, troughs):
        closes += list(np.linspace(level, p, leg + 1)[1:])
        level = p
        closes += list(np.linspace(level, t, leg + 1)[1:])
        level = t
    return closes


def test_llm_disabled_uses_structure_read_not_neutral():
    closes = _zigzag_closes(100.0, peaks=[104, 110, 116], troughs=[101, 106, 112])
    bars = make_session_bars(closes, bar_range=0.2)
    agent = VisionAgent(llm_enabled=False)
    ev = asyncio.run(agent.evaluate(AnalysisContext(ticker="TEST", bars=bars)))
    assert ev.reasoning["provider"] == "code_structure"
    assert ev.confidence > 0.1           # a real read, not the "insufficient data" floor


def test_structure_higher_highs_and_lows_scores_bullish():
    # Each swing high and swing low is above the one before it — clean uptrend.
    closes = _zigzag_closes(100.0, peaks=[104, 110, 116], troughs=[101, 106, 112])
    bars = make_session_bars(closes, bar_range=0.2)
    agent = VisionAgent(llm_enabled=False)
    score, pattern, detail = agent._structure_score(bars)
    assert pattern == "uptrend_structure"
    assert score > 55.0
    assert detail["swing_high_count"] >= 2 and detail["swing_low_count"] >= 2


def test_structure_lower_highs_and_lows_scores_bearish():
    closes = _zigzag_closes(120.0, peaks=[116, 110, 104], troughs=[119, 114, 108])
    bars = make_session_bars(closes, bar_range=0.2)
    agent = VisionAgent(llm_enabled=False)
    score, pattern, detail = agent._structure_score(bars)
    assert pattern == "downtrend_structure"
    assert score < 45.0


def test_structure_breakout_above_last_swing_high_scores_strongly_bullish():
    closes = _zigzag_closes(100.0, peaks=[104, 110, 116], troughs=[101, 106, 112])
    closes += list(np.linspace(closes[-1], 122.0, 5))[1:]   # push well above the last peak
    bars = make_session_bars(closes, bar_range=0.2)
    agent = VisionAgent(llm_enabled=False)
    score, pattern, detail = agent._structure_score(bars)
    assert pattern == "resistance_breakout"
    assert score >= 80.0


def test_structure_insufficient_bars_is_the_only_neutral_case():
    bars = make_session_bars([100.0] * 10)   # below _MIN_BARS
    agent = VisionAgent(llm_enabled=False)
    ev = asyncio.run(agent.evaluate(AnalysisContext(ticker="TEST", bars=bars)))
    assert ev.score == 50.0
    assert "insufficient bars" in ev.rationale


def test_swing_point_plateau_is_deduped_to_one_point():
    # A near-tied double-top must not compare a peak against itself and read
    # as "not higher" — see VisionAgent._dedupe_adjacent.
    closes = _zigzag_closes(100.0, peaks=[104, 110, 116], troughs=[101, 106, 112])
    bars = make_session_bars(closes, bar_range=0.2)
    swing_highs, swing_lows = VisionAgent._swing_points(bars)
    # No two consecutive indices — each cluster collapsed to one point.
    assert all(b - a > 1 for a, b in zip(swing_highs, swing_highs[1:]))
    assert all(b - a > 1 for a, b in zip(swing_lows, swing_lows[1:]))
