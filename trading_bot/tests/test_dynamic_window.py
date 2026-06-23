"""Dynamic lookback sizing — choose_window_days picks the smallest window that
holds enough trades for a trustworthy result, from observed trade density.

Smart, case-by-case: dense signals → short recent window; sparse signals →
longer window to clear the statistical minimums; bounded by [floor, cap].
"""
from datetime import datetime, timedelta, timezone

import pandas as pd

from backtest_intraday import choose_window_days, data_span_days, trim_bars, LOOKBACK_BARS


# walk-forward defaults used by the optimizer
WF = dict(floor=30, cap=120, min_is=20, min_oos=6, split_frac=0.70)
# single-run defaults used by the standalone backtest
SR = dict(floor=30, cap=120, min_is=20, min_oos=0, split_frac=1.0)


def test_no_trades_falls_back_to_cap():
    assert choose_window_days(0, 60, **WF) == 120
    assert choose_window_days(10, 0, **WF) == 120


def test_dense_signals_clamp_to_floor():
    # 600 trades over 120d = 5/day. 30d easily clears 20 IS + 6 OOS → floor.
    assert choose_window_days(600, 120, **WF) == 30


def test_sparse_signals_extend_window():
    # ~0.5 trades/day. Needs ~74d (OOS binds) — between floor and cap.
    d = choose_window_days(60, 120, **WF)
    assert 30 < d < 120


def test_very_sparse_clamps_to_cap():
    # ~0.08 trades/day → would need >>120d, clamp to cap.
    assert choose_window_days(10, 120, **WF) == 120


def test_oos_constraint_can_bind_harder_than_in_sample():
    # With split 0.70/0.30, the 30% OOS slice is the scarcer one; the chosen
    # window must satisfy it (>=6 OOS trades * margin).
    d = choose_window_days(40, 120, **WF)
    density = 40 / 120
    oos_trades = density * d * (1 - WF["split_frac"])
    assert oos_trades >= WF["min_oos"]


def test_single_run_ignores_oos():
    # Same density, but single-run mode (min_oos=0) needs a shorter window than
    # walk-forward, since it only has to clear the in-sample minimum.
    assert choose_window_days(40, 120, **SR) <= choose_window_days(40, 120, **WF)


def test_respects_cap_below_floor_is_safe():
    # Degenerate config: cap < floor must not explode; result stays <= cap.
    assert choose_window_days(5, 120, floor=90, cap=45, min_is=20, min_oos=6, split_frac=0.7) <= 45


def test_result_is_int_within_bounds():
    for trades in (5, 25, 100, 300):
        d = choose_window_days(trades, 100, **WF)
        assert isinstance(d, int) and WF["floor"] <= d <= WF["cap"]


def _bars(n, end, freq_min=5):
    idx = pd.date_range(end=end, periods=n, freq=f"{freq_min}min", tz="UTC")
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}, index=idx)


def test_data_span_days_uses_longest_series():
    end = pd.Timestamp("2026-06-23T20:00:00Z")
    short = _bars(100, end)                                   # ~8h
    long  = pd.DataFrame({"close": [1, 2]},
                         index=[end - timedelta(days=40), end])
    assert data_span_days({"A": short, "B": long}) == 40
    assert data_span_days({}) == 0


def test_trim_bars_keeps_recent_and_drops_too_short():
    end = datetime(2026, 6, 23, 20, 0, tzinfo=timezone.utc)
    # plenty of bars spanning ~30 days → survives a 10-day trim
    big = _bars(LOOKBACK_BARS + 5000, pd.Timestamp(end))
    # a tiny recent series → too short after trim, must be dropped
    tiny = _bars(20, pd.Timestamp(end))
    out = trim_bars({"BIG": big, "TINY": tiny}, days=10, end_dt=end)
    assert "BIG" in out and "TINY" not in out
    assert out["BIG"].index[0] >= pd.Timestamp(end - timedelta(days=10))
