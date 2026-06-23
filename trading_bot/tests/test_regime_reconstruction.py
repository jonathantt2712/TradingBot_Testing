"""Historical regime reconstruction for the backtest.

The backtest rebuilds the live risk_on/neutral/risk_off label point-in-time
(prior-day VIX, session VWAP/day-change up to entry) using the SAME pure
classifier the live agent uses, then tags each trade so results can be split
per regime.
"""
import datetime as dt

import pandas as pd
import pytest

pytest.importorskip("pandas")

from agents.regime_agent import classify_regime, MarketRegime, _VIX_THRESHOLDS
import backtest_intraday as b


# ── classify_regime (shared live+backtest rule) ──────────────────────────────

def _c(**kw):
    base = dict(vix_level=None, vix_thresholds=_VIX_THRESHOLDS,
                spy_vs_vwap=None, spy_day_chg=None, qqq_vs_vwap=None, qqq_day_chg=None)
    base.update(kw)
    return classify_regime(**base)[0]


def test_vix_spike_forces_risk_off():
    assert _c(vix_level=30, spy_vs_vwap=1, spy_day_chg=1) is MarketRegime.RISK_OFF


def test_index_waterfall_forces_risk_off_even_with_low_vix():
    assert _c(vix_level=12, spy_vs_vwap=-0.5, spy_day_chg=-1.2) is MarketRegime.RISK_OFF


def test_two_signals_needed_for_risk_on():
    # low VIX (1 signal) + SPY confirm (2nd) → risk_on
    assert _c(vix_level=14, spy_vs_vwap=0.5, spy_day_chg=0.2) is MarketRegime.RISK_ON
    # only SPY confirm, VIX mid-range (no signal) → neutral
    assert _c(vix_level=21, spy_vs_vwap=0.5, spy_day_chg=0.2) is MarketRegime.NEUTRAL


# ── _prior_vix (no same-day look-ahead) ──────────────────────────────────────

def test_prior_vix_excludes_same_day():
    m = {dt.date(2026, 6, 19): 14.0, dt.date(2026, 6, 22): 99.0}
    assert b._prior_vix(m, dt.date(2026, 6, 22)) == 14.0  # not 99 (same day)
    assert b._prior_vix(m, dt.date(2026, 6, 18)) is None  # nothing earlier


# ── regime_at (point-in-time reconstruction) ─────────────────────────────────

def _session(close_path):
    idx = pd.date_range("2026-06-22 13:30", periods=len(close_path), freq="5min", tz="UTC")
    return pd.DataFrame({"open": close_path[0], "high": [c + 1 for c in close_path],
                         "low": [c - 1 for c in close_path], "close": close_path,
                         "volume": 1000}, index=idx)


def test_regime_at_risk_on_and_risk_off():
    up = _session([100 + i * 0.1 for i in range(20)])
    entry = up.index[-1]
    assert b.regime_at(entry, up, up, {dt.date(2026, 6, 19): 14.0}) == "risk_on"
    assert b.regime_at(entry, up, up, {dt.date(2026, 6, 19): 30.0}) == "risk_off"


def test_regime_at_ignores_future_bars():
    # bars after entry must not influence the label (no look-ahead)
    path = [100 + i * 0.1 for i in range(10)] + [50] * 10   # crashes AFTER entry
    sess = _session(path)
    entry = sess.index[9]   # before the crash
    assert b.regime_at(entry, sess, sess, {dt.date(2026, 6, 19): 14.0}) == "risk_on"


# ── calc_summary by_regime breakdown ─────────────────────────────────────────

def _tr(regime, pnl, outcome):
    return b.TradeResult(
        ticker="X", direction="LONG", entry_time="2026-06-22 14:00:00+00:00",
        exit_time="2026-06-22 15:00:00+00:00", entry_price=100, exit_price=101,
        qty=1, stop_loss=99, take_profit=102, risk_reward=2.0,
        outcome=outcome, pnl_usd=pnl, pnl_pct=1.0, score=70, regime=regime,
    )


def test_calc_summary_breaks_down_by_regime():
    trades = [_tr("risk_on", 100, "TP_HIT"), _tr("risk_on", -50, "SL_HIT"),
              _tr("risk_off", -30, "SL_HIT")]
    by = {r["regime"]: r for r in b.calc_summary(trades)["by_regime"]}
    assert by["risk_on"]["trades"] == 2 and by["risk_on"]["pnl"] == 50
    assert by["risk_off"]["trades"] == 1 and by["risk_off"]["win_rate"] == 0.0
