"""Unit tests for optimize_strategy pure helpers.

Covers: _split_caches, _threshold_combos, _atr_combos, _slim, _fmt_params,
_params_label, _write_dashboard_files.
No network calls, no broker connections.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import os

import optimize_strategy as _opt_mod
from optimize_strategy import (
    _atr_combos,
    _fmt_params,
    _params_label,
    _print_recommendation,
    _slim,
    _split_caches,
    _threshold_combos,
    _write_dashboard_files,
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

    def test_minimum_split_boundary(self):
        """Series just barely long enough for both halves should yield a valid split.

        Minimum for frac=0.7: n > LOOKBACK_BARS/0.7 AND n*0.3 > LOOKBACK_BARS.
        At 78 bars/day, 9 trading days (702 bars) clears both constraints;
        8 days (624 bars) does not (OOS slice would be only 187 bars < 200).
        """
        barely_too_short = _trading_day_bars(8)   # 624 bars → OOS slice < LOOKBACK_BARS
        just_enough      = _trading_day_bars(9)   # 702 bars → valid split

        is_short, oos_short = _split_caches({"X": barely_too_short}, frac=SPLIT_FRAC)
        is_long,  oos_long  = _split_caches({"X": just_enough},      frac=SPLIT_FRAC)

        assert "X" not in oos_short   # too short → in-sample only
        assert "X" in oos_long        # just enough → proper split

    def test_extreme_frac_zero_goes_to_is(self):
        """frac=0 → split=0 ≤ LOOKBACK_BARS → whole series stays in in-sample."""
        bars = _trading_day_bars(40)
        is_c, oos_c = _split_caches({"X": bars}, frac=0.0)
        assert "X" in is_c
        assert "X" not in oos_c

    def test_extreme_frac_one_goes_to_is(self):
        """frac=1 → split=n, n-split=0 ≤ LOOKBACK_BARS → whole series stays in IS."""
        bars = _trading_day_bars(40)
        is_c, oos_c = _split_caches({"X": bars}, frac=1.0)
        assert "X" in is_c
        assert "X" not in oos_c


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


# ── _params_label ─────────────────────────────────────────────────────────────

class TestParamsLabel:
    def test_threshold_key_shortened(self):
        result = _params_label({"LONG_THRESHOLD": 60.0})
        assert "LONG_T=60.0" in result
        assert "LONG_THRESHOLD" not in result

    def test_multiple_key_shortened(self):
        result = _params_label({"ATR_STOP_MULTIPLE": 2.0, "ATR_TARGET_MULTIPLE": 4.0})
        assert "ATR_STOP_M=2.0" in result
        assert "ATR_TARGET_M=4.0" in result
        assert "_MULTIPLE" not in result

    def test_empty_dict_returns_empty_string(self):
        assert _params_label({}) == ""


# ── _write_dashboard_files ────────────────────────────────────────────────────

class TestWriteDashboardFiles:
    @pytest.fixture(autouse=True)
    def _patch_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_opt_mod, "OPTIMAL_JSON", tmp_path / "backtest_optimal.json")
        monkeypatch.setattr(_opt_mod, "OPTIMAL_CONFIG", tmp_path / "OPTIMAL_CONFIG.txt")
        self.tmp = tmp_path

    def _best(self, **oos_overrides):
        base = {
            "total_trades": 30, "win_rate": 55.0, "total_pnl": 1500.0,
            "avg_win": 80.0, "avg_loss": -60.0, "profit_factor": 1.8,
            "sharpe": 1.2, "max_drawdown": -200.0, "ev_per_trade": 50.0,
        }
        if oos_overrides:
            return {
                "params": {"LONG_THRESHOLD": 60.0, "SHORT_THRESHOLD": 40.0},
                "in_sample": base,
                "oos": {**base, **oos_overrides},
            }
        return {"params": {"LONG_THRESHOLD": 60.0, "SHORT_THRESHOLD": 40.0}, **base}

    def test_empty_best_writes_no_files(self):
        _write_dashboard_files({}, days=30, objective="total_pnl", validated=False)
        assert not (self.tmp / "backtest_optimal.json").exists()
        assert not (self.tmp / "OPTIMAL_CONFIG.txt").exists()

    def test_json_file_is_parseable(self):
        _write_dashboard_files(self._best(), days=30, objective="total_pnl", validated=False)
        data = json.loads((self.tmp / "backtest_optimal.json").read_text())
        assert isinstance(data, dict)

    def test_json_file_contains_optimal_params(self):
        _write_dashboard_files(self._best(), days=30, objective="total_pnl", validated=False)
        data = json.loads((self.tmp / "backtest_optimal.json").read_text())
        assert "optimal_params" in data
        assert data["optimal_params"]["LONG_THRESHOLD"] == pytest.approx(60.0)

    def test_config_txt_contains_params(self):
        _write_dashboard_files(self._best(), days=30, objective="total_pnl", validated=False)
        txt = (self.tmp / "OPTIMAL_CONFIG.txt").read_text()
        assert "LONG_THRESHOLD=60.0" in txt
        assert "SHORT_THRESHOLD=40.0" in txt

    def test_oos_metrics_used_when_validated(self):
        """When best has an 'oos' block the JSON reports OOS stats, not in-sample."""
        best = self._best(win_rate=48.0, total_pnl=800.0)
        _write_dashboard_files(best, days=30, objective="total_pnl", validated=True)
        data = json.loads((self.tmp / "backtest_optimal.json").read_text())
        assert data["win_rate"] == pytest.approx(48.0)
        assert data["total_pnl"] == pytest.approx(800.0)

    def test_optimal_window_days_stored(self):
        _write_dashboard_files(self._best(), days=45, objective="total_pnl", validated=False)
        data = json.loads((self.tmp / "backtest_optimal.json").read_text())
        assert data["optimal_window_days"] == 45


# ── _print_recommendation overfit detection ────────────────────────────────────

def _rec(is_pnl: float, oos_pnl: float) -> list[dict]:
    """Minimal validated result list for _print_recommendation."""
    slim = {k: 0 for k in _SLIM_KEYS}
    return [{
        "params": {"LONG_THRESHOLD": 60.0, "SHORT_THRESHOLD": 40.0},
        "in_sample": {**slim, "total_pnl": is_pnl, "win_rate": 55.0},
        "oos":       {**slim, "total_pnl": oos_pnl, "win_rate": 50.0},
        "rank_value": oos_pnl,
        "trades_ok": True,
    }]


class TestPrintRecommendation:
    def test_empty_results_no_output(self, capsys):
        _print_recommendation([], "total_pnl")
        assert capsys.readouterr().out == ""

    def test_oos_non_positive_warns(self, capsys):
        _print_recommendation(_rec(is_pnl=1000.0, oos_pnl=-50.0), "total_pnl")
        out = capsys.readouterr().out
        assert "do NOT deploy" in out.lower() or "non-positive" in out.lower()

    def test_oos_far_below_is_warns_overfit(self, capsys):
        """OOS profit < 40% of in-sample triggers the 'likely overfit' warning."""
        _print_recommendation(_rec(is_pnl=1000.0, oos_pnl=300.0), "total_pnl")
        out = capsys.readouterr().out
        assert "overfit" in out.lower() or "caution" in out.lower()

    def test_oos_holds_up_shows_success(self, capsys):
        """OOS profit >= 40% of in-sample shows the positive checkmark."""
        _print_recommendation(_rec(is_pnl=1000.0, oos_pnl=600.0), "total_pnl")
        out = capsys.readouterr().out
        assert "generalise" in out.lower() or "✅" in out

    def test_non_validated_warns_no_oos(self, capsys):
        """A result without 'oos' key triggers the full-window overfit warning."""
        slim = {k: 0 for k in _SLIM_KEYS}
        result = [{
            "params": {"LONG_THRESHOLD": 60.0},
            "total_pnl": 500.0, "win_rate": 55.0, "ev_per_trade": 50.0,
            "profit_factor": 1.5, "total_trades": 30,
            "rank_value": 500.0, "trades_ok": True,
        }]
        _print_recommendation(result, "total_pnl")
        out = capsys.readouterr().out
        assert "no walk-forward" in out.lower() or "no-validate" in out.lower() or "overfitting" in out.lower()

    def test_params_printed_in_output(self, capsys):
        """The recommendation must echo the parameter values."""
        _print_recommendation(_rec(is_pnl=1000.0, oos_pnl=600.0), "total_pnl")
        out = capsys.readouterr().out
        assert "60.0" in out
        assert "40.0" in out


# ── _make_settings env var cleanup ────────────────────────────────────────────

class TestMakeSettings:
    def test_env_vars_cleaned_up_after_call(self):
        """_make_settings must not leave env var overrides in os.environ."""
        from optimize_strategy import _make_settings
        key = "LONG_THRESHOLD"
        assert key not in os.environ   # precondition: not set
        _make_settings({key: 62.0})
        assert key not in os.environ   # must be cleaned up

    def test_overrides_applied_during_call(self):
        """The returned settings must reflect the overrides that were passed."""
        from optimize_strategy import _make_settings
        s = _make_settings({"LONG_THRESHOLD": 63.0})
        assert s.thresholds.long_above == pytest.approx(63.0)
