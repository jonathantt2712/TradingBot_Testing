"""Unit tests for backtest_runner — simulate_fill.

Only the pure fill simulator is tested here; network-dependent helpers
(fetch_historical_bars, get_recommendations, backtest_ticker) are not.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backtest_runner import simulate_fill
from core.enums import Decision

_ET = ZoneInfo("America/New_York")


# ── helpers ────────────────────────────────────────────────────────────────────

def _bars(prices: list[tuple[float, float, float, float]], start_ts: datetime) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from (open, high, low, close) tuples."""
    idx = pd.date_range(start_ts, periods=len(prices), freq="5min", tz="UTC")
    return pd.DataFrame(
        [{"open": o, "high": h, "low": l, "close": c, "volume": 1000} for o, h, l, c in prices],
        index=idx,
    )


def _rth_ts(hour: int = 9, minute: int = 35) -> datetime:
    """A UTC timestamp for a given ET time during RTH."""
    return datetime(2026, 6, 16, hour, minute, tzinfo=_ET).astimezone(timezone.utc)


# ── LONG fills ─────────────────────────────────────────────────────────────────

class TestSimulateFillLong:
    def test_tp_hit(self):
        entry, sl, tp = 100.0, 98.0, 103.0
        bars = _bars([(100, 104, 99, 101)], _rth_ts())
        outcome, exit_px, _, pnl, _ = simulate_fill(
            bars, direction=Decision.LONG, entry=entry, stop_loss=sl, take_profit=tp, qty=10,
        )
        assert outcome == "TP_HIT"
        assert exit_px == pytest.approx(103.0)
        assert pnl == pytest.approx(30.0)

    def test_sl_hit(self):
        entry, sl, tp = 100.0, 97.0, 104.0
        bars = _bars([(100, 101, 96, 100)], _rth_ts())
        outcome, exit_px, _, pnl, _ = simulate_fill(
            bars, direction=Decision.LONG, entry=entry, stop_loss=sl, take_profit=tp, qty=5,
        )
        assert outcome == "SL_HIT"
        assert exit_px == pytest.approx(97.0)
        assert pnl == pytest.approx(-15.0)

    def test_both_in_same_bar_worst_case_sl(self):
        """When both TP and SL are touched in one bar, SL is assumed (worst-case)."""
        entry, sl, tp = 100.0, 97.0, 103.0
        bars = _bars([(100, 104, 96, 100)], _rth_ts())
        outcome, _, _, _, _ = simulate_fill(
            bars, direction=Decision.LONG, entry=entry, stop_loss=sl, take_profit=tp, qty=1,
        )
        assert outcome == "SL_HIT"

    def test_timeout_no_hit(self):
        entry, sl, tp = 100.0, 90.0, 115.0
        bars = _bars([(100, 101, 99, 100)] * 5, _rth_ts())
        outcome, exit_px, _, _, _ = simulate_fill(
            bars, direction=Decision.LONG, entry=entry, stop_loss=sl, take_profit=tp,
            qty=1, max_bars=5,
        )
        assert outcome == "TIMEOUT"
        assert exit_px == pytest.approx(100.0)

    def test_pnl_pct_long(self):
        entry, sl, tp = 200.0, 190.0, 210.0
        bars = _bars([(200, 215, 199, 211)], _rth_ts())
        _, exit_px, _, pnl, pnl_pct = simulate_fill(
            bars, direction=Decision.LONG, entry=entry, stop_loss=sl, take_profit=tp, qty=2,
        )
        assert exit_px == pytest.approx(210.0)
        assert pnl == pytest.approx(20.0)
        assert pnl_pct == pytest.approx(5.0)

    def test_tp_not_hit_until_second_bar(self):
        entry, sl, tp = 100.0, 95.0, 103.0
        bars = _bars([
            (100, 102, 99, 101),   # high < tp → no hit
            (101, 104, 100, 103),  # high >= tp → TP
        ], _rth_ts())
        outcome, _, _, _, _ = simulate_fill(
            bars, direction=Decision.LONG, entry=entry, stop_loss=sl, take_profit=tp, qty=1,
        )
        assert outcome == "TP_HIT"

    def test_timeout_exits_at_max_bars_close_not_beyond(self):
        """When max_bars < len(bars) the exit must use bar[max_bars-1].close,
        not any later bar — the bars beyond max_bars are never examined."""
        entry, sl, tp = 100.0, 50.0, 200.0
        # 5 safe bars then 1 that would hit TP if examined
        bars = _bars(
            [(100, 101, 99, 100)] * 3 + [(100, 250, 99, 250)],
            _rth_ts(),
        )
        outcome, exit_px, _, _, _ = simulate_fill(
            bars, direction=Decision.LONG, entry=entry, stop_loss=sl, take_profit=tp,
            qty=1, max_bars=3,
        )
        assert outcome == "TIMEOUT"
        assert exit_px == pytest.approx(100.0)   # close of bar[2], not 250


# ── SHORT fills ────────────────────────────────────────────────────────────────

class TestSimulateFillShort:
    def test_tp_hit_short(self):
        """SHORT TP when low <= take_profit."""
        entry, sl, tp = 100.0, 103.0, 97.0
        bars = _bars([(100, 101, 96, 99)], _rth_ts())
        outcome, exit_px, _, pnl, _ = simulate_fill(
            bars, direction=Decision.SHORT, entry=entry, stop_loss=sl, take_profit=tp, qty=10,
        )
        assert outcome == "TP_HIT"
        assert exit_px == pytest.approx(97.0)
        assert pnl == pytest.approx(30.0)

    def test_sl_hit_short(self):
        """SHORT SL when high >= stop_loss."""
        entry, sl, tp = 100.0, 103.0, 96.0
        bars = _bars([(100, 104, 99, 101)], _rth_ts())
        outcome, exit_px, _, pnl, _ = simulate_fill(
            bars, direction=Decision.SHORT, entry=entry, stop_loss=sl, take_profit=tp, qty=5,
        )
        assert outcome == "SL_HIT"
        assert exit_px == pytest.approx(103.0)
        assert pnl == pytest.approx(-15.0)

    def test_pnl_pct_short(self):
        entry, sl, tp = 200.0, 210.0, 190.0
        bars = _bars([(200, 201, 188, 195)], _rth_ts())
        _, exit_px, _, pnl, pnl_pct = simulate_fill(
            bars, direction=Decision.SHORT, entry=entry, stop_loss=sl, take_profit=tp, qty=2,
        )
        assert exit_px == pytest.approx(190.0)
        assert pnl == pytest.approx(20.0)
        assert pnl_pct == pytest.approx(5.0)


# ── EOD forced exit ────────────────────────────────────────────────────────────

class TestSimulateFillEmpty:
    def test_empty_bars_returns_none(self):
        """simulate_fill must return None (not crash) when bars_after_entry is empty."""
        empty = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], tz="UTC"),
        )
        result = simulate_fill(
            empty, direction=Decision.LONG, entry=100.0,
            stop_loss=95.0, take_profit=105.0, qty=1,
        )
        assert result is None


class TestSimulateFillEOD:
    def test_eod_exit_at_1555_et(self):
        """Bar exactly at 15:55 ET triggers intraday forced exit."""
        entry, sl, tp = 100.0, 95.0, 108.0
        eod_ts = _rth_ts(hour=15, minute=55)
        bars = _bars([(100, 101, 99, 100.5)], eod_ts)
        outcome, exit_px, _, _, _ = simulate_fill(
            bars, direction=Decision.LONG, entry=entry, stop_loss=sl, take_profit=tp, qty=1,
        )
        assert outcome == "TIMEOUT"
        assert exit_px == pytest.approx(100.5)

    def test_eod_exit_after_1555_et(self):
        """Bar after 15:55 ET (e.g. 16:00) also triggers forced exit."""
        entry, sl, tp = 100.0, 95.0, 108.0
        ts_1600 = _rth_ts(hour=16, minute=0)
        bars = _bars([(100, 101, 99, 100.2)], ts_1600)
        outcome, exit_px, _, _, _ = simulate_fill(
            bars, direction=Decision.LONG, entry=entry, stop_loss=sl, take_profit=tp, qty=1,
        )
        assert outcome == "TIMEOUT"
        assert exit_px == pytest.approx(100.2)

    def test_no_eod_exit_before_1555(self):
        """Bar at 15:50 ET (before cutoff) still allows TP to be triggered."""
        entry, sl, tp = 100.0, 95.0, 103.0
        ts_1550 = _rth_ts(hour=15, minute=50)
        bars = _bars([(100, 104, 99, 103)], ts_1550)
        outcome, _, _, _, _ = simulate_fill(
            bars, direction=Decision.LONG, entry=entry, stop_loss=sl, take_profit=tp, qty=1,
        )
        assert outcome == "TP_HIT"

    def test_eod_exit_has_correct_pnl(self):
        """EOD close-price P&L is computed correctly for LONG."""
        entry, sl, tp = 100.0, 95.0, 108.0
        close_px = 101.5
        eod_ts = _rth_ts(hour=15, minute=55)
        bars = _bars([(100, 102, 99, close_px)], eod_ts)
        _, _, _, pnl, pnl_pct = simulate_fill(
            bars, direction=Decision.LONG, entry=entry, stop_loss=sl, take_profit=tp, qty=4,
        )
        assert pnl == pytest.approx((close_px - entry) * 4)
        assert pnl_pct == pytest.approx((close_px - entry) / entry * 100)

    def test_eod_exit_short_correct_pnl(self):
        """EOD P&L is computed correctly for SHORT (price dropped → profit)."""
        entry, sl, tp = 100.0, 105.0, 95.0
        close_px = 98.0
        eod_ts = _rth_ts(hour=15, minute=55)
        bars = _bars([(100, 101, 97, close_px)], eod_ts)
        _, _, _, pnl, pnl_pct = simulate_fill(
            bars, direction=Decision.SHORT, entry=entry, stop_loss=sl, take_profit=tp, qty=2,
        )
        assert pnl == pytest.approx((entry - close_px) * 2)
        assert pnl_pct == pytest.approx((entry - close_px) / entry * 100)
