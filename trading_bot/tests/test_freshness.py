"""bar_staleness: fail-closed freshness check for OHLCV series."""
import asyncio
from datetime import timedelta, timezone

import pandas as pd

from agents.risk_agent import RiskAgent
from config.settings import RiskConfig
from core.enums import Decision
from core.freshness import bar_staleness
from core.models import AnalysisContext

from conftest import make_session_bars


def _recent_bars(n: int = 30, *, last_age_min: float = 0.0):
    """5-min session bars whose final bar sits `last_age_min` minutes in the past."""
    end = pd.Timestamp.now(tz=timezone.utc) - timedelta(minutes=last_age_min)
    idx = pd.date_range(end=end, periods=n, freq="5min")
    return pd.DataFrame(
        {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 10_000},
        index=idx,
    )


def test_fresh_series_is_not_stale():
    stale, reason = bar_staleness(_recent_bars(last_age_min=1.0))
    assert stale is False
    assert reason is None


def test_old_last_bar_is_stale():
    # Last bar 40 min old against a 5-min cadence (>3x) → stale.
    stale, reason = bar_staleness(_recent_bars(last_age_min=40.0))
    assert stale is True
    assert "stale" in reason


def test_factor_zero_disables_check():
    stale, _ = bar_staleness(_recent_bars(last_age_min=1_000.0), max_age_factor=0)
    assert stale is False


def test_too_few_bars_is_stale():
    stale, reason = bar_staleness(_recent_bars(n=2))
    assert stale is True
    assert "insufficient" in reason


def test_none_bars_is_stale():
    stale, _ = bar_staleness(None)
    assert stale is True


def test_tz_naive_index_handled():
    # tz-naive timestamps are assumed UTC and must not raise.
    bars = _recent_bars(last_age_min=1.0)
    bars.index = bars.index.tz_localize(None)
    stale, _ = bar_staleness(bars)
    assert stale is False


def test_explicit_now_makes_dated_fixture_stale():
    bars = make_session_bars([100.0] * 30)  # dated 2026-06-09
    later = bars.index[-1] + timedelta(hours=2)
    assert bar_staleness(bars, now=later)[0] is True
    fresh = bars.index[-1] + timedelta(minutes=5)
    assert bar_staleness(bars, now=fresh)[0] is False


# ── RiskAgent integration ────────────────────────────────────────────────────

def test_risk_agent_vetoes_stale_live_data():
    agent = RiskAgent(RiskConfig())
    ctx = AnalysisContext(ticker="TEST", bars=_recent_bars(last_age_min=120.0),
                          account={"equity": 100_000.0})
    ev = asyncio.run(agent.evaluate(ctx))
    assert ev.veto
    assert "stale" in ev.rationale


def test_risk_agent_skips_freshness_in_backtest():
    # Dated historical bars must NOT be vetoed for staleness in backtest mode.
    agent = RiskAgent(RiskConfig())
    bars = make_session_bars([100.0] * 29 + [101.0])
    bars.loc[bars.index[-1], "high"] = 101.0
    ctx = AnalysisContext(ticker="TEST", bars=bars,
                          account={"equity": 100_000.0}, backtest_mode=True)
    ev = asyncio.run(agent.evaluate(ctx))
    # May or may not veto on R/R, but never with a staleness reason.
    assert "stale" not in ev.rationale
