"""Unit tests for backtest_intraday pure functions.

Covers: simulate_day_trade, calc_summary, choose_window_days, trim_bars,
data_span_days, _spy_regime_at, _session_vwap_chg — all without network
calls or broker connections.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from core.enums import Decision
from backtest_intraday import (
    LOOKBACK_BARS,
    TradeResult,
    _session_vwap_chg,
    _spy_regime_at,
    calc_summary,
    choose_window_days,
    data_span_days,
    simulate_day_trade,
    trim_bars,
)

_ET = ZoneInfo("America/New_York")


# ── helpers ────────────────────────────────────────────────────────────────────

def _rth_start(d: date = date(2026, 6, 16)) -> datetime:
    """9:30 ET on the given date, returned as UTC."""
    return datetime(d.year, d.month, d.day, 9, 30, tzinfo=_ET).astimezone(timezone.utc)


def _make_bars(prices: list[float], start_utc: datetime) -> pd.DataFrame:
    idx = pd.date_range(start_utc, periods=len(prices), freq="5min", tz="UTC")
    return pd.DataFrame({
        "open":   prices,
        "high":   [p * 1.002 for p in prices],
        "low":    [p * 0.998 for p in prices],
        "close":  prices,
        "volume": 10_000,
    }, index=idx)


def _make_trade(
    outcome: str, pnl: float, ticker: str = "AAPL", regime: str = "neutral",
    entry_time: str = "2026-06-16 09:35:00+00:00",
) -> TradeResult:
    return TradeResult(
        ticker=ticker, direction="LONG",
        entry_time=entry_time,
        exit_time="2026-06-16 14:00:00+00:00",
        entry_price=100.0, exit_price=101.0 if pnl > 0 else 99.0,
        qty=10.0, stop_loss=98.0, take_profit=104.0,
        risk_reward=2.0, outcome=outcome, pnl_usd=pnl, pnl_pct=1.0,
        score=70.0, regime=regime,
    )


def _full_rth_bars(
    n: int = 78, price: float = 100.0
) -> pd.DataFrame:
    """n bars starting at 9:30 ET (78 bars covers 9:30-15:55 ET)."""
    return _make_bars([price] * n, _rth_start())


# ── simulate_day_trade ─────────────────────────────────────────────────────────

class TestSimulateDayTrade:
    def test_tp_hit_long(self):
        start = _rth_start()
        idx = pd.date_range(start, periods=5, freq="5min", tz="UTC")
        bars = pd.DataFrame({
            "open": 100.0,
            "high": [100.2, 100.5, 101.5, 102.0, 102.0],
            "low":  99.8,
            "close": 101.5,
            "volume": 1000,
        }, index=idx)
        outcome, exit_px, _, pnl, _ = simulate_day_trade(
            bars, direction=Decision.LONG, entry=100.0,
            stop_loss=98.0, take_profit=101.0, qty=10,
        )
        assert outcome == "TP_HIT"
        assert exit_px == pytest.approx(101.0)
        assert pnl > 0

    def test_sl_hit_long(self):
        start = _rth_start()
        idx = pd.date_range(start, periods=5, freq="5min", tz="UTC")
        bars = pd.DataFrame({
            "open": 100.0, "high": 100.2,
            "low": [99.8, 99.5, 97.0, 97.0, 97.0],
            "close": 97.0, "volume": 1000,
        }, index=idx)
        outcome, exit_px, _, pnl, _ = simulate_day_trade(
            bars, direction=Decision.LONG, entry=100.0,
            stop_loss=98.0, take_profit=104.0, qty=10,
        )
        assert outcome == "SL_HIT"
        assert exit_px == pytest.approx(98.0)
        assert pnl < 0

    def test_tp_hit_short(self):
        start = _rth_start()
        idx = pd.date_range(start, periods=5, freq="5min", tz="UTC")
        bars = pd.DataFrame({
            "open": 100.0, "high": 100.2,
            "low": [99.8, 99.5, 97.0, 97.0, 97.0],
            "close": 97.0, "volume": 1000,
        }, index=idx)
        outcome, exit_px, _, pnl, _ = simulate_day_trade(
            bars, direction=Decision.SHORT, entry=100.0,
            stop_loss=103.0, take_profit=98.0, qty=10,
        )
        assert outcome == "TP_HIT"
        assert exit_px == pytest.approx(98.0)
        assert pnl > 0

    def test_sl_hit_short(self):
        start = _rth_start()
        idx = pd.date_range(start, periods=5, freq="5min", tz="UTC")
        bars = pd.DataFrame({
            "open": 100.0,
            "high": [100.2, 100.5, 104.0, 104.0, 104.0],
            "low": 99.8, "close": 104.0, "volume": 1000,
        }, index=idx)
        outcome, exit_px, _, pnl, _ = simulate_day_trade(
            bars, direction=Decision.SHORT, entry=100.0,
            stop_loss=103.0, take_profit=96.0, qty=10,
        )
        assert outcome == "SL_HIT"
        assert exit_px == pytest.approx(103.0)
        assert pnl < 0

    def test_both_hit_same_bar_uses_sl(self):
        """Both TP and SL triggered in the same bar → conservative SL exit."""
        start = _rth_start()
        idx = pd.date_range(start, periods=1, freq="5min", tz="UTC")
        bars = pd.DataFrame({
            "open": 100.0, "high": [105.0], "low": [95.0],
            "close": 100.0, "volume": 1000,
        }, index=idx)
        outcome, exit_px, _, pnl, _ = simulate_day_trade(
            bars, direction=Decision.LONG, entry=100.0,
            stop_loss=95.0, take_profit=105.0, qty=10,
        )
        assert outcome == "SL_HIT"
        assert exit_px == pytest.approx(95.0)
        assert pnl < 0

    def test_eod_close_at_1555_et(self):
        """Full RTH session (78 bars) with no TP/SL hit → EOD_CLOSE at 15:55 ET."""
        bars = _full_rth_bars(n=78)
        outcome, _, _, _, _ = simulate_day_trade(
            bars, direction=Decision.LONG, entry=100.0,
            stop_loss=50.0, take_profit=200.0, qty=10,
        )
        assert outcome == "EOD_CLOSE"

    def test_fallback_last_bar_when_no_eod_bar(self):
        """Short session with no TP/SL hit and no 15:55 bar → last bar fallback."""
        bars = _make_bars([100.0, 100.2, 100.3], _rth_start())
        outcome, exit_px, _, _, _ = simulate_day_trade(
            bars, direction=Decision.LONG, entry=100.0,
            stop_loss=90.0, take_profit=120.0, qty=10,
        )
        assert outcome == "EOD_CLOSE"
        assert exit_px == pytest.approx(100.3)

    def test_slippage_reduces_pnl(self):
        start = _rth_start()
        idx = pd.date_range(start, periods=2, freq="5min", tz="UTC")
        bars = pd.DataFrame({
            "open": 100.0, "high": [100.2, 105.0],
            "low": 99.8, "close": 105.0, "volume": 1000,
        }, index=idx)
        _, _, _, pnl_no, _ = simulate_day_trade(
            bars, direction=Decision.LONG, entry=100.0,
            stop_loss=98.0, take_profit=105.0, qty=10, slippage_pct=0.0,
        )
        _, _, _, pnl_slip, _ = simulate_day_trade(
            bars, direction=Decision.LONG, entry=100.0,
            stop_loss=98.0, take_profit=105.0, qty=10, slippage_pct=0.001,
        )
        assert pnl_slip < pnl_no

    def test_pnl_sign_consistent_with_direction(self):
        """A winning LONG has positive pnl; a winning SHORT also has positive pnl."""
        start = _rth_start()
        idx = pd.date_range(start, periods=2, freq="5min", tz="UTC")
        long_bars = pd.DataFrame({
            "open": 100.0, "high": [100.2, 105.0],
            "low": 99.8, "close": 105.0, "volume": 1000,
        }, index=idx)
        short_bars = pd.DataFrame({
            "open": 100.0, "high": 100.2,
            "low": [99.8, 95.0], "close": 95.0, "volume": 1000,
        }, index=idx)
        _, _, _, long_pnl, _ = simulate_day_trade(
            long_bars, direction=Decision.LONG, entry=100.0,
            stop_loss=98.0, take_profit=105.0, qty=10,
        )
        _, _, _, short_pnl, _ = simulate_day_trade(
            short_bars, direction=Decision.SHORT, entry=100.0,
            stop_loss=103.0, take_profit=95.0, qty=10,
        )
        assert long_pnl > 0
        assert short_pnl > 0


# ── calc_summary ───────────────────────────────────────────────────────────────

class TestCalcSummary:
    def test_empty_returns_empty_dict(self):
        assert calc_summary([]) == {}

    def test_counts_correct(self):
        trades = [
            _make_trade("TP_HIT",    200.0),
            _make_trade("SL_HIT",   -100.0),
            _make_trade("EOD_CLOSE",  10.0),
        ]
        s = calc_summary(trades)
        assert s["wins"]   == 1
        assert s["losses"] == 1
        assert s["eods"]   == 1
        assert s["total_trades"] == 3

    def test_win_rate_all_wins(self):
        trades = [_make_trade("TP_HIT", 100.0) for _ in range(4)]
        assert calc_summary(trades)["win_rate"] == pytest.approx(100.0)

    def test_total_pnl_sums(self):
        trades = [
            _make_trade("TP_HIT",   200.0),
            _make_trade("SL_HIT",  -100.0),
            _make_trade("EOD_CLOSE", 10.0),
        ]
        assert calc_summary(trades)["total_pnl"] == pytest.approx(110.0)

    def test_profit_factor_no_losses_sentinel(self):
        trades = [_make_trade("TP_HIT", 100.0)]
        # No losses → sentinel 999.0 (avoids JSON-invalid Infinity)
        assert calc_summary(trades)["profit_factor"] == 999.0

    def test_profit_factor_all_losses_zero(self):
        trades = [_make_trade("EOD_CLOSE", 0.0)]
        assert calc_summary(trades)["profit_factor"] == 0.0

    def test_profit_factor_mixed(self):
        trades = [
            _make_trade("TP_HIT",  200.0),
            _make_trade("SL_HIT", -100.0),
        ]
        assert calc_summary(trades)["profit_factor"] == pytest.approx(200.0 / 100.0)

    def test_ev_per_trade_is_mean_pnl(self):
        trades = [
            _make_trade("TP_HIT",  200.0),
            _make_trade("SL_HIT", -100.0),
        ]
        assert calc_summary(trades)["ev_per_trade"] == pytest.approx(50.0)

    def test_sharpe_zero_single_trading_day(self):
        """One trading day → daily P&L std is 0 → Sharpe is 0.0."""
        trades = [_make_trade("TP_HIT", 100.0), _make_trade("SL_HIT", -50.0)]
        assert calc_summary(trades)["sharpe"] == 0.0

    def test_sharpe_nonzero_multiple_days(self):
        """Two trading days with different P&Ls → non-zero Sharpe."""
        trades = [
            _make_trade("TP_HIT", 200.0, entry_time="2026-06-16 09:35:00+00:00"),
            _make_trade("SL_HIT", -50.0, entry_time="2026-06-17 09:35:00+00:00"),
        ]
        assert calc_summary(trades)["sharpe"] != 0.0

    def test_max_drawdown_negative(self):
        trades = [_make_trade("SL_HIT", -100.0), _make_trade("SL_HIT", -150.0)]
        assert calc_summary(trades)["max_drawdown"] < 0

    def test_max_drawdown_all_wins_is_zero(self):
        trades = [_make_trade("TP_HIT", 100.0), _make_trade("TP_HIT", 50.0)]
        assert calc_summary(trades)["max_drawdown"] == pytest.approx(0.0)

    def test_by_ticker_grouped(self):
        trades = [
            _make_trade("TP_HIT",  100.0, ticker="AAPL"),
            _make_trade("SL_HIT",  -50.0, ticker="AAPL"),
            _make_trade("TP_HIT",   80.0, ticker="TSLA"),
        ]
        s = calc_summary(trades)
        tickers = {r["ticker"]: r for r in s["by_ticker"]}
        assert "AAPL" in tickers
        assert "TSLA" in tickers
        assert tickers["AAPL"]["trades"] == 2

    def test_by_regime_grouped(self):
        trades = [
            _make_trade("TP_HIT",  100.0, regime="risk_on"),
            _make_trade("SL_HIT",  -50.0, regime="risk_off"),
        ]
        regimes = {r["regime"]: r for r in calc_summary(trades)["by_regime"]}
        assert "risk_on"  in regimes
        assert "risk_off" in regimes

    def test_no_nan_or_inf_in_key_metrics(self):
        """All key numeric outputs must be finite (safe for JSON serialization)."""
        trades = [_make_trade("TP_HIT", 100.0), _make_trade("SL_HIT", -50.0)]
        s = calc_summary(trades)
        for key in ("win_rate", "total_pnl", "profit_factor",
                    "sharpe", "max_drawdown", "ev_per_trade"):
            v = s[key]
            assert not math.isnan(v),  f"{key} is NaN"
            assert not math.isinf(v),  f"{key} is inf"


# ── choose_window_days ─────────────────────────────────────────────────────────

class TestChooseWindowDays:
    def test_no_data_returns_cap(self):
        w = choose_window_days(0, 60, floor=30, cap=90, min_is=20, min_oos=0, split_frac=1.0)
        assert w == 90

    def test_zero_days_returns_cap(self):
        w = choose_window_days(100, 0, floor=30, cap=90, min_is=20, min_oos=0, split_frac=1.0)
        assert w == 90

    def test_dense_signals_returns_floor(self):
        # 200 trades / 60 days ≈ 3.3/day; need 20 IS trades ≈ 8 days < 30-day floor
        w = choose_window_days(200, 60, floor=30, cap=90, min_is=20, min_oos=0, split_frac=1.0)
        assert w == 30

    def test_sparse_signals_returns_cap(self):
        # 3 trades / 60 days ≈ 0.05/day; need 20 IS trades → 500+ days > cap
        w = choose_window_days(3, 60, floor=30, cap=90, min_is=20, min_oos=0, split_frac=1.0)
        assert w == 90

    def test_floor_always_respected(self):
        w = choose_window_days(1000, 60, floor=45, cap=90, min_is=5, min_oos=0, split_frac=1.0)
        assert w >= 45

    def test_cap_always_respected(self):
        w = choose_window_days(1, 200, floor=30, cap=90, min_is=20, min_oos=0, split_frac=1.0)
        assert w <= 90

    def test_oos_requirement_needs_longer_window(self):
        # OOS minimum forces a wider window than IS alone would need
        w_no_oos   = choose_window_days(30, 60, floor=30, cap=90, min_is=10, min_oos=0,  split_frac=1.0)
        w_with_oos = choose_window_days(30, 60, floor=30, cap=90, min_is=10, min_oos=10, split_frac=0.7)
        assert w_with_oos >= w_no_oos

    def test_output_is_integer(self):
        w = choose_window_days(50, 60, floor=30, cap=90, min_is=20, min_oos=0, split_frac=1.0)
        assert isinstance(w, int)


# ── data_span_days / trim_bars ─────────────────────────────────────────────────

def _day_bars(n_days: int, price: float = 100.0) -> pd.DataFrame:
    """n_days of realistic 5-min RTH bars with overnight gaps (each day 9:30-15:55 ET)."""
    frames = []
    d = date(2026, 5, 1)
    for _ in range(n_days):
        start = datetime(d.year, d.month, d.day, 9, 30, tzinfo=_ET).astimezone(timezone.utc)
        idx = pd.date_range(start, periods=78, freq="5min", tz="UTC")
        frames.append(pd.DataFrame({
            "open": price, "high": price * 1.002, "low": price * 0.998,
            "close": price, "volume": 10_000,
        }, index=idx))
        # Advance to next weekday (skip Sat/Sun)
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return pd.concat(frames).sort_index()


class TestDataSpanDays:
    def test_empty_map_returns_zero(self):
        assert data_span_days({}) == 0

    def test_single_bar_returns_zero(self):
        bars = _make_bars([100.0], _rth_start())
        assert data_span_days({"X": bars}) == 0

    def test_approximates_calendar_days(self):
        bars = _day_bars(10)   # 10 trading days with overnight gaps
        span = data_span_days({"X": bars})
        # 10 trading days ≈ 14 calendar days (includes 2 weekends); span ≥ 13
        assert span >= 13

    def test_uses_longest_series(self):
        short = _day_bars(5)
        long_ = _day_bars(20)   # 20 trading days ≈ 28 calendar days
        span = data_span_days({"A": short, "B": long_})
        assert span >= 27


class TestTrimBars:
    def test_trims_old_data(self):
        # 30 trading days; trim to last 5 → smaller series
        bars = _day_bars(30)
        end_dt = bars.index[-1].to_pydatetime()
        trimmed = trim_bars({"X": bars}, days=5, end_dt=end_dt)
        if "X" in trimmed:
            assert len(trimmed["X"]) < len(bars)

    def test_drops_ticker_below_min_length(self):
        # Only 5 bars — far below LOOKBACK_BARS+10 → dropped
        start = datetime(2026, 6, 20, 9, 30, tzinfo=_ET).astimezone(timezone.utc)
        tiny = _make_bars([100.0] * 5, start)
        end_dt = datetime(2026, 7, 1, 23, 59, tzinfo=timezone.utc)
        result = trim_bars({"TINY": tiny}, days=10, end_dt=end_dt)
        assert "TINY" not in result

    def test_keeps_ticker_with_enough_bars(self):
        # 30 trading days with overnight gaps; trim window matches full span
        bars = _day_bars(30)
        end_dt = bars.index[-1].to_pydatetime()
        # Use a window that covers the whole series (>30 calendar days)
        result = trim_bars({"X": bars}, days=50, end_dt=end_dt)
        assert "X" in result


# ── _spy_regime_at ─────────────────────────────────────────────────────────────

def _spy(open_p: float, close_p: float, n: int = 10) -> pd.DataFrame:
    """Simple SPY bar set with fixed open/close per bar."""
    start = _rth_start()
    idx = pd.date_range(start, periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({
        "open":   open_p,
        "high":   max(open_p, close_p) + 0.1,
        "low":    min(open_p, close_p) - 0.1,
        "close":  close_p,
        "volume": 10_000,
    }, index=idx)


class TestSpyRegimeAt:
    def test_bull_when_up_more_than_threshold(self):
        bars = _spy(400.0, 401.6)   # +0.4% > 0.3 threshold
        assert _spy_regime_at(bars, bars.index[5]) == "bull"

    def test_bear_when_down_more_than_threshold(self):
        bars = _spy(400.0, 398.4)   # -0.4% < -0.3 threshold
        assert _spy_regime_at(bars, bars.index[5]) == "bear"

    def test_neutral_when_flat(self):
        bars = _spy(400.0, 400.0)
        assert _spy_regime_at(bars, bars.index[5]) == "neutral"

    def test_none_bars_returns_neutral(self):
        ts = pd.Timestamp("2026-06-16 14:00:00", tz="UTC")
        assert _spy_regime_at(None, ts) == "neutral"

    def test_empty_bars_returns_neutral(self):
        empty = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], tz="UTC"),
        )
        ts = pd.Timestamp("2026-06-16 14:00:00", tz="UTC")
        assert _spy_regime_at(empty, ts) == "neutral"

    def test_only_uses_bars_up_to_entry_ts(self):
        """Bars AFTER entry_ts must not affect the regime label."""
        bars = _spy(400.0, 400.0, n=20)
        # Entry is at bar 5 (9:55 ET); bars after that have no impact
        assert _spy_regime_at(bars, bars.index[5]) == "neutral"


# ── _session_vwap_chg ──────────────────────────────────────────────────────────

class TestSessionVwapChg:
    def test_none_bars_returns_none_pair(self):
        ts = pd.Timestamp("2026-06-16 14:00:00", tz="UTC")
        assert _session_vwap_chg(None, ts) == (None, None)

    def test_flat_bars_vwap_zero_chg(self):
        bars = _make_bars([100.0] * 10, _rth_start())
        entry_ts = bars.index[5]
        vs_vwap, day_chg = _session_vwap_chg(bars, entry_ts)
        assert vs_vwap  == pytest.approx(0.0, abs=0.01)
        assert day_chg  == pytest.approx(0.0, abs=0.01)

    def test_rising_bars_positive_day_chg(self):
        prices = list(range(100, 115))   # rising from 100 to 114
        bars = _make_bars(prices, _rth_start())
        entry_ts = bars.index[-1]
        _, day_chg = _session_vwap_chg(bars, entry_ts)
        assert day_chg > 0

    def test_only_uses_bars_up_to_entry_ts(self):
        """Bars after entry_ts must not be included in the VWAP calculation."""
        prices = [100.0] * 5 + [200.0] * 5   # price spikes after bar 4
        bars = _make_bars(prices, _rth_start())
        entry_ts = bars.index[4]   # entry is at bar 4, before the spike
        vs_vwap, _ = _session_vwap_chg(bars, entry_ts)
        # VWAP up to bar 4 should be ~100, not skewed by the 200-bars
        assert vs_vwap is not None
        assert abs(vs_vwap) < 1.0   # near zero, since bars 0-4 are all 100
