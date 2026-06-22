"""MacroSignalAgent — 4-factor macro lean from free daily bars.

The scoring (`_build_snapshot`) and return helper (`_pct_return`) are pure, so
they're tested directly without any network. The backtest no-lookahead guard is
also pinned.
"""
import asyncio

import pytest

from agents.macro_agent import MacroSignalAgent, _build_snapshot, _pct_return
from core.enums import AgentRole
from core.models import AnalysisContext


# ── _pct_return ──────────────────────────────────────────────────────────────

def test_pct_return_basic():
    closes = [100.0] * 7 + [110.0]   # +10% over 7-day lookback
    assert _pct_return(closes, 7) == pytest.approx(10.0)


def test_pct_return_insufficient_history():
    assert _pct_return([100.0, 101.0], 7) is None


def test_pct_return_guards_zero_base():
    assert _pct_return([0.0] + [100.0] * 7, 7) is None


# ── _build_snapshot scoring ──────────────────────────────────────────────────

def _rising(base, pct, n=25):
    """A close series ending +pct% over a 20-day lookback."""
    return [base] * (n - 1) + [base * (1 + pct / 100)]


def test_all_none_is_neutral_low_confidence():
    score, conf, rationale = _build_snapshot(None, None, None, None, None)
    assert score == 50.0
    assert conf == 0.20
    assert "no data" in rationale


def test_bullish_factors_push_long():
    # BTC up, QQQ up, QQQ>XLP spread positive, no safe-haven pressure
    score, conf, rationale = _build_snapshot(
        btc_closes=[100.0] * 7 + [110.0],     # +10% 7d
        qqq_closes=_rising(100.0, 10.0),       # +10% 20d
        xlp_closes=_rising(100.0, 0.0),        # flat → positive spread
        gld_closes=_rising(100.0, 0.0),
        uup_closes=_rising(100.0, 0.0),
    )
    assert score > 55.0
    assert "bullish" in rationale
    assert conf == 0.60   # >= 3 signals present


def test_safe_haven_pressure_is_inverse():
    # Rising gold/dollar should pull the score DOWN (risk-off).
    score, _conf, rationale = _build_snapshot(
        btc_closes=None, qqq_closes=None, xlp_closes=None,
        gld_closes=_rising(100.0, 10.0),   # gold +10%
        uup_closes=_rising(100.0, 0.0),
    )
    assert score < 50.0
    assert "bearish" in rationale


def test_score_stays_in_valid_range_on_extremes():
    score, _c, _r = _build_snapshot(
        btc_closes=[100.0] * 7 + [200.0],      # +100%
        qqq_closes=_rising(100.0, 100.0),
        xlp_closes=_rising(100.0, -50.0),
        gld_closes=_rising(100.0, -50.0),
        uup_closes=_rising(100.0, -50.0),
    )
    assert 1.0 <= score <= 100.0


# ── evaluate guards ──────────────────────────────────────────────────────────

def test_backtest_mode_is_neutral_no_lookahead():
    agent = MacroSignalAgent()
    ctx = AnalysisContext(ticker="NVDA", bars=None, account={"equity": 1.0}, backtest_mode=True)
    ev = asyncio.run(agent.evaluate(ctx))
    assert ev.role is AgentRole.MACRO
    assert ev.score == 50.0
    assert ev.confidence == 0.0
