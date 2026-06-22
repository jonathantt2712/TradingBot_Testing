"""SqueezeAgent — FINRA short-volume setup detection.

Network is never touched: ``_fetch_finra`` is monkeypatched to return a
controlled short-ratio map. These tests pin the setup classification and the
``setup`` tag the PortfolioManager keys its squeeze boost off.
"""
import asyncio

import numpy as np
import pandas as pd
import pytest

from agents import squeeze_agent
from agents.squeeze_agent import SqueezeAgent
from core.enums import AgentRole
from core.models import AnalysisContext


def _multiday_bars(day_specs):
    """Build an intraday OHLCV frame spanning multiple ET days.

    ``day_specs`` is a list of (date_str, closes, volume_per_bar). Opens follow
    the prior close so a rising close-list reads as an up day.
    """
    frames = []
    for date_str, closes, vol in day_specs:
        idx = pd.date_range(f"{date_str} 13:30:00", periods=len(closes), freq="5min", tz="UTC")
        closes_arr = np.asarray(closes, dtype=float)
        opens = np.concatenate([[closes_arr[0]], closes_arr[:-1]])
        frames.append(pd.DataFrame({
            "open": opens,
            "high": np.maximum(opens, closes_arr) + 0.5,
            "low": np.minimum(opens, closes_arr) - 0.5,
            "close": closes_arr,
            "volume": [vol] * len(closes),
        }, index=idx))
    return pd.concat(frames)


def _ctx(bars, ticker="GME"):
    return AnalysisContext(ticker=ticker, bars=bars, account={"equity": 100_000})


def _patch_finra(monkeypatch, mapping):
    async def fake(*_a, **_k):
        return dict(mapping)
    monkeypatch.setattr(squeeze_agent, "_fetch_finra", fake)


def _run(agent, ctx):
    return asyncio.run(agent.evaluate(ctx))


# ── neutral / no-data paths ──────────────────────────────────────────────────

def test_backtest_mode_is_neutral_no_lookahead():
    agent = SqueezeAgent()
    ctx = AnalysisContext(ticker="GME", bars=None, account={"equity": 1.0}, backtest_mode=True)
    ev = _run(agent, ctx)
    assert ev.role is AgentRole.SQUEEZE
    assert ev.score == 50.0
    assert ev.confidence == 0.0


def test_no_finra_data_for_ticker_is_neutral(monkeypatch):
    _patch_finra(monkeypatch, {})
    agent = SqueezeAgent()
    ev = _run(agent, _ctx(_multiday_bars([("2026-06-15", [100] * 5, 1000)])))
    assert ev.score == 50.0
    assert ev.confidence == 0.05
    assert "no FINRA" in ev.rationale


# ── squeeze classification ───────────────────────────────────────────────────

def test_high_short_price_up_high_relvol_is_squeeze_long(monkeypatch):
    _patch_finra(monkeypatch, {"GME": 0.70})
    bars = _multiday_bars([
        ("2026-06-15", [100.0] * 20, 100),   # prior day, low volume
        ("2026-06-16", [100.0] * 20, 100),   # prior day, low volume
        ("2026-06-17", [100, 101, 102, 103, 104], 2000),  # today: up + big volume
    ])
    ev = _run(SqueezeAgent(), _ctx(bars))
    assert ev.data["setup"] == "squeeze_long"
    assert ev.score > 65.0          # confirmed squeeze clears the action band
    assert ev.data["rel_vol"] > 2.0


def test_high_short_price_up_low_relvol_is_capped(monkeypatch):
    """An unconfirmed squeeze (no volume surge) is capped below the action band."""
    _patch_finra(monkeypatch, {"GME": 0.70})
    bars = _multiday_bars([
        ("2026-06-16", [100.0] * 20, 1000),
        ("2026-06-17", [100, 101, 102, 103, 104], 1000),  # up but flat volume
    ])
    ev = _run(SqueezeAgent(), _ctx(bars))
    assert ev.data["setup"] == "squeeze_long"
    assert ev.score <= 65.0
    assert ev.data["rel_vol"] <= 2.0


def test_high_short_price_down_is_short_pressure(monkeypatch):
    _patch_finra(monkeypatch, {"GME": 0.70})
    bars = _multiday_bars([
        ("2026-06-16", [100.0] * 20, 1000),
        ("2026-06-17", [104, 103, 102, 101, 100], 1000),  # down day
    ])
    ev = _run(SqueezeAgent(), _ctx(bars))
    assert ev.data["setup"] == "short_pressure"
    assert ev.score < 50.0


def test_moderate_short_ratio(monkeypatch):
    _patch_finra(monkeypatch, {"GME": 0.40})
    bars = _multiday_bars([
        ("2026-06-16", [100.0] * 20, 1000),
        ("2026-06-17", [100, 101, 102, 103, 104], 1000),
    ])
    ev = _run(SqueezeAgent(), _ctx(bars))
    assert ev.data["setup"] == "moderate_short"
    assert 50.0 <= ev.score <= 56.0


def test_low_short_ratio_is_neutral(monkeypatch):
    _patch_finra(monkeypatch, {"GME": 0.10})
    bars = _multiday_bars([
        ("2026-06-16", [100.0] * 20, 1000),
        ("2026-06-17", [100, 101, 102, 103, 104], 1000),
    ])
    ev = _run(SqueezeAgent(), _ctx(bars))
    assert ev.data["setup"] == "low_short"
    assert ev.score == 50.0


def test_score_always_in_valid_range(monkeypatch):
    _patch_finra(monkeypatch, {"GME": 0.85})
    bars = _multiday_bars([
        ("2026-06-15", [100.0] * 20, 100),
        ("2026-06-16", [100.0] * 20, 100),
        ("2026-06-17", [100, 102, 104, 106, 108], 5000),
    ])
    ev = _run(SqueezeAgent(), _ctx(bars))
    assert 1.0 <= ev.score <= 100.0
    assert 0.0 <= ev.confidence <= 0.70
