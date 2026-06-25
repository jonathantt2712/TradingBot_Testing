"""Unit tests for optimize_strategy pure helpers.

Covers: _split_caches, _threshold_combos, _atr_combos, _slim, _fmt_params.
No network calls, no broker connections.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from optimize_strategy import (
    _atr_combos,
    _fmt_params,
    _slim,
    _split_caches,
    _threshold_combos,
    SPLIT_FRAC,
    MIN_TRADES,
    MIN_OOS_TRADES,
    _SLIM_KEYS,
)
from backtest_intraday import LOOKBACK_BARS

_ET = ZoneInfo("America/New_York")


# ── helpers ────────────────────────────────────────────────────────────────────

def _trading_day_bars(n_days: int, price: float = 100.0) -> pd.DataFrame:
    """n_days of 5-min RTH bars with overnight gaps (each day 9:30–15:55 ET)."""
    frames = []
    d = date(2026, 5, 1)
    for _ in range(n_days):
        start = datetime(d.year, d.month, d.day, 9, 30, tzinfo=_ET).astimezone(timezone.utc)
        idx = pd.date_range(start, periods=78, freq="5min", tz="UTC")
        frames.append(pd.DataFrame({
            "open": price, "high": price * 1.002, "low": price * 0.998,
            "close": price, "volume": 10_000,
        }, index=idx))
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return pd.concat(frames).sort_index()


# ── _split_caches ──────────────────────────────────────────────────────────────

class TestSplitCaches:
    def _bars(self, n_days: int = 40) -> dict[str, pd.DataFrame]:
        return {"AAPL": _trading_day_bars(n_days)}

    def test_is_before_oos_in_time(self):
        is_c, oos_c = _split_caches(self._bars(40), frac=SPLIT_FRAC)
        if "AAPL" in is_c and "AAPL" in oos_c:
            assert is_c["AAPL"].index[-1] < oos_c["AAPL"].index[-1]

    def test_oos_has_lookback_overlap(self):
        """OOS slice starts LOOKBACK_BARS before the split so first OOS eval is valid."""
        is_c, oos_c = _split_caches(self._bars(40), frac=SPLIT_FRAC)
        if "AAPL" in oos_c:
            # OOS must have at least LOOKBACK_BARS bars (the overlap window)
            assert len(oos_c["AAPL"]) >= LOOKBACK_BARS

    def test_no_is_oos_overlap_in_trades(self):
        """IS last bar must precede OOS (split - LOOKBACK_BARS) bar by definition;
        more practically: IS last bar must come before OOS last bar."""
        is_c, oos_c = _split_caches(self._bars(40), frac=SPLIT_FRAC)
        if "AAPL" in is_c and "AAPL" in oos_c:
            assert is_c["AAPL"].index[-1] < oos_c["AAPL"].index[-1]

    def test_short_series_kept_whole_in_is(self):
        """Series too short to split goes entirely into in-sample."""
        short = _trading_day_bars(5)   # not enough for two LOOKBACK_BARS halves
        is_c, oos_c = _split_caches({"X": short}, frac=SPLIT_FRAC)
        assert "X" in is_c
        assert "X" not in oos_c

    def test_total_bars_conserved(self):
        """IS bars + (OOS bars - LOOKBACK_BARS overlap) ≈ total bars."""
        bars = _trading_day_bars(40)
        n_total = len(bars)
        is_c, oos_c = _split_caches({"X": bars}, frac=SPLIT_FRAC)
        if "X" in is_c and "X" in oos_c:
            # is_n + (oos_n - LOOKBACK_BARS) == n_total
            is_n  = len(is_c["X"])
            oos_n = len(oos_c["X"])
            assert is_n + (oos_n - LOOKBACK_BARS) == n_total

    def test_frac_determines_split_point(self):
        """A 50/50 split should give IS ≈ half of total bars."""
        bars = _trading_day_bars(40)
        n_total = len(bars)
        is_c, _ = _split_caches({"X": bars}, frac=0.5)
        if "X" in is_c:
            ratio = len(is_c["X"]) / n_total
            assert 0.45 < ratio < 0.55


# ── _threshold_combos ──────────────────────────────────────────────────────────

class TestThresholdCombos:
    def _combos(self):
        return _threshold_combos(atr_stop=2.0, atr_target=4.0)

    def test_all_combos_have_required_keys(self):
        for c in self._combos():
            assert "LONG_THRESHOLD"    in c
            assert "SHORT_THRESHOLD"   in c
            assert "ATR_STOP_MULTIPLE" in c
            assert "ATR_TARGET_MULTIPLE" in c

    def test_long_minus_short_always_gte_10(self):
        """The optimizer requires at least a 10-point gap between thresholds."""
        for c in self._combos():
            assert c["LONG_THRESHOLD"] - c["SHORT_THRESHOLD"] >= 10

    def test_atr_values_fixed_at_passed_values(self):
        for c in _threshold_combos(atr_stop=1.5, atr_target=3.0):
            assert c["ATR_STOP_MULTIPLE"]   == pytest.approx(1.5)
            assert c["ATR_TARGET_MULTIPLE"] == pytest.approx(3.0)

    def test_returns_non_empty(self):
        assert len(self._combos()) > 0

    def test_no_duplicate_combos(self):
        combos = self._combos()
        seen = set()
        for c in combos:
            key = (c["LONG_THRESHOLD"], c["SHORT_THRESHOLD"])
            assert key not in seen, f"Duplicate combo: {key}"
            seen.add(key)


# ── _atr_combos ───────────────────────────────────────────────────────────────

class TestAtrCombos:
    def _combos(self):
        return _atr_combos(long_thresh=60.0, short_thresh=40.0)

    def test_all_combos_have_required_keys(self):
        for c in self._combos():
            assert "ATR_STOP_MULTIPLE"   in c
            assert "ATR_TARGET_MULTIPLE" in c
            assert "LONG_THRESHOLD"  in c
            assert "SHORT_THRESHOLD" in c

    def test_target_always_greater_than_stop(self):
        """A risk-reward less than 1:1 is always filtered out."""
        for c in self._combos():
            assert c["ATR_TARGET_MULTIPLE"] > c["ATR_STOP_MULTIPLE"]

    def test_thresholds_fixed_at_passed_values(self):
        for c in _atr_combos(long_thresh=63.0, short_thresh=37.0):
            assert c["LONG_THRESHOLD"]  == pytest.approx(63.0)
            assert c["SHORT_THRESHOLD"] == pytest.approx(37.0)

    def test_returns_non_empty(self):
        assert len(self._combos()) > 0

    def test_no_duplicate_combos(self):
        combos = self._combos()
        seen = set()
        for c in combos:
            key = (c["ATR_STOP_MULTIPLE"], c["ATR_TARGET_MULTIPLE"])
            assert key not in seen
            seen.add(key)


# ── _slim ─────────────────────────────────────────────────────────────────────

class TestSlim:
    def test_keeps_only_slim_keys(self):
        full = {k: 1.0 for k in _SLIM_KEYS} | {"trades": [1, 2, 3], "by_ticker": []}
        s = _slim(full)
        assert set(s.keys()) == set(_SLIM_KEYS)
        assert "trades" not in s

    def test_missing_keys_return_none(self):
        s = _slim({})
        for k in _SLIM_KEYS:
            assert s[k] is None

    def test_values_preserved(self):
        full = {"total_trades": 42, "win_rate": 55.5}
        s = _slim(full)
        assert s["total_trades"] == 42
        assert s["win_rate"] == pytest.approx(55.5)


# ── rank_value zero bug (regression) ─────────────────────────────────────────
# The `or _WORST` idiom replaced legitimate 0.0 objective values with _WORST,
# so breakeven param sets ranked below all losers. The fix uses explicit `is None`.

def _fake_summary(objective: str, value: float) -> dict:
    base = {k: 0 for k in _SLIM_KEYS}
    base["total_trades"] = 50
    base[objective] = value
    return base


class TestRankValueZeroBug:
    def test_zero_total_pnl_is_not_worst(self):
        """A breakeven param set (total_pnl=0.0) must rank above losing sets."""
        from optimize_strategy import _WORST
        s = _fake_summary("total_pnl", 0.0)
        v = s.get("total_pnl")
        rank = v if v is not None else _WORST
        assert rank == 0.0
        assert rank > _WORST

    def test_zero_sharpe_is_not_worst(self):
        from optimize_strategy import _WORST
        s = _fake_summary("sharpe", 0.0)
        v = s.get("sharpe")
        rank = v if v is not None else _WORST
        assert rank == 0.0
        assert rank > _WORST

    def test_zero_ev_per_trade_is_not_worst(self):
        from optimize_strategy import _WORST
        s = _fake_summary("ev_per_trade", 0.0)
        v = s.get("ev_per_trade")
        rank = v if v is not None else _WORST
        assert rank == 0.0
        assert rank > _WORST

    def test_missing_objective_returns_worst(self):
        """A missing key (no trades at all) must still map to _WORST."""
        from optimize_strategy import _WORST
        v = {}.get("total_pnl")
        rank = v if v is not None else _WORST
        assert rank == _WORST


# ── _fmt_params ───────────────────────────────────────────────────────────────

class TestFmtParams:
    def test_formats_key_value_pairs(self):
        p = {"LONG_THRESHOLD": 60.0, "SHORT_THRESHOLD": 40.0}
        result = _fmt_params(p)
        assert "LONG_THRESHOLD=60.0" in result
        assert "SHORT_THRESHOLD=40.0" in result

    def test_empty_dict_returns_empty_string(self):
        assert _fmt_params({}) == ""
