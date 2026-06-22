"""LiquidAgent — equity flow quality (rel-vol, VWAP dev, momentum, spread).

All signals are derived from the OHLCV frame already in the context (no
network). Tests pin the directional behaviour and the no-data guards.
"""
import asyncio

import numpy as np
import pandas as pd

from agents.liquid_agent import LiquidAgent
from core.enums import AgentRole
from core.models import AnalysisContext


def _multiday_bars(day_specs):
    frames = []
    for date_str, closes, vol in day_specs:
        idx = pd.date_range(f"{date_str} 13:30:00", periods=len(closes), freq="5min", tz="UTC")
        closes_arr = np.asarray(closes, dtype=float)
        opens = np.concatenate([[closes_arr[0]], closes_arr[:-1]])
        frames.append(pd.DataFrame({
            "open": opens,
            "high": np.maximum(opens, closes_arr) + 0.2,
            "low": np.minimum(opens, closes_arr) - 0.2,
            "close": closes_arr,
            "volume": [vol] * len(closes),
        }, index=idx))
    return pd.concat(frames)


def _ctx(bars):
    return AnalysisContext(ticker="NVDA", bars=bars, account={"equity": 100_000})


def _run(bars):
    return asyncio.run(LiquidAgent().evaluate(_ctx(bars)))


# ── guards ───────────────────────────────────────────────────────────────────

def test_insufficient_bars_is_neutral():
    bars = _multiday_bars([("2026-06-17", [100.0] * 5, 1000)])  # < 20 bars
    ev = _run(bars)
    assert ev.role is AgentRole.LIQUID
    assert ev.score == 50.0
    assert ev.confidence == 0.05
    assert "insufficient" in ev.rationale


# ── directional behaviour ────────────────────────────────────────────────────

# rel-vol needs >= 5 distinct prior days, so these fixtures span a week.
_QUIET_WEEK = [(f"2026-06-{d:02d}", [100.0] * 20, 100) for d in range(10, 17)]


def test_uptrend_high_relvol_reads_bullish():
    bars = _multiday_bars(_QUIET_WEEK + [
        ("2026-06-17", [100, 101, 102, 103, 104], 3000),  # up + volume surge
    ])
    ev = _run(bars)
    assert ev.score > 55.0
    assert ev.reasoning["relative_volume"] > 2.0
    assert ev.reasoning["intraday_direction"] == "up"


def test_downtrend_high_relvol_reads_bearish():
    bars = _multiday_bars(_QUIET_WEEK + [
        ("2026-06-17", [104, 103, 102, 101, 100], 3000),  # down + volume surge
    ])
    ev = _run(bars)
    assert ev.score < 45.0
    assert ev.reasoning["intraday_direction"] == "down"


def test_score_always_in_valid_range_and_confidence_capped():
    bars = _multiday_bars([
        ("2026-06-15", [100.0] * 20, 50),
        ("2026-06-16", [100, 105, 110, 115, 120], 10000),  # violent up move
    ])
    ev = _run(bars)
    assert 1.0 <= ev.score <= 100.0
    assert 0.25 <= ev.confidence <= 0.90


def test_rationale_lists_subsignals():
    bars = _multiday_bars(_QUIET_WEEK + [
        ("2026-06-17", [100, 101, 102, 103, 104], 2000),
    ])
    ev = _run(bars)
    # rationale is "k=v | k=v" — every sub-signal that fired should appear
    assert "rel_vol=" in ev.rationale
    for key in ev.data["signals"]:
        assert f"{key}=" in ev.rationale
