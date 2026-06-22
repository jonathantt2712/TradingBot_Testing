"""InsiderAgent — congressional-disclosure scoring + technical confirmation gate.

Network is mocked: we seed the agent's in-memory cache with recent-dated
transactions and let `_get_transactions` do its real ticker/date filtering,
or patch it directly for the scoring-only cases.
"""
import asyncio
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from agents.insider_agent import InsiderAgent
from core.enums import AgentRole
from core.models import AnalysisContext


def _today_iso():
    return datetime.now(timezone.utc).date().isoformat()


def _txn(rep, ttype, amount="$15,001 - $50,000", ticker="NVDA", date=None):
    return {
        "representative": rep,
        "type": ttype,
        "amount": amount,
        "ticker": ticker,
        "disclosure_date": date or _today_iso(),
    }


def _trending_bars(up: bool, n: int = 40):
    """Bars with a clear up/down intraday drift so tech confirms align."""
    idx = pd.date_range("2026-06-17 13:30:00", periods=n, freq="5min", tz="UTC")
    step = 0.5 if up else -0.5
    closes = np.array([100.0 + step * i for i in range(n)], dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame({
        "open": opens,
        "high": np.maximum(opens, closes) + 0.2,
        "low": np.minimum(opens, closes) - 0.2,
        "close": closes,
        "volume": [10_000] * n,
    }, index=idx)


def _ctx(bars):
    return AnalysisContext(ticker="NVDA", bars=bars, account={"equity": 100_000})


def _seed(agent, txns):
    agent._cache = txns
    agent._cache_ts = time.monotonic()


def _run(agent, ctx):
    return asyncio.run(agent.evaluate(ctx))


# ── guards ───────────────────────────────────────────────────────────────────

def test_backtest_mode_is_neutral_no_lookahead():
    agent = InsiderAgent()
    ctx = AnalysisContext(ticker="NVDA", bars=None, account={"equity": 1.0}, backtest_mode=True)
    ev = _run(agent, ctx)
    assert ev.role is AgentRole.INSIDER
    assert ev.score == 50.0
    assert ev.confidence == 0.0


def test_no_transactions_is_neutral():
    agent = InsiderAgent()
    _seed(agent, [])
    ev = _run(agent, _ctx(_trending_bars(up=True)))
    assert ev.score == 50.0
    assert "no congressional trades" in ev.rationale


def test_stale_transactions_filtered_out():
    agent = InsiderAgent()
    _seed(agent, [_txn("Rep A", "purchase", date="2020-01-01")])  # way out of window
    ev = _run(agent, _ctx(_trending_bars(up=True)))
    assert ev.score == 50.0


# ── classification ───────────────────────────────────────────────────────────

def test_net_buying_with_tech_confirmation_is_bullish():
    agent = InsiderAgent()
    _seed(agent, [
        _txn("Rep A", "purchase", "$250,001 - $500,000"),
        _txn("Rep B", "purchase", "$100,001 - $250,000"),
        _txn("Rep C", "purchase"),
    ])
    ev = _run(agent, _ctx(_trending_bars(up=True)))  # uptrend → tech confirms
    assert ev.score > 60.0
    assert "congress buyers" in ev.rationale
    assert ev.reasoning["unique_buyers"] == 3


def test_net_buying_without_tech_confirmation_is_capped():
    agent = InsiderAgent()
    _seed(agent, [_txn("Rep A", "purchase"), _txn("Rep B", "purchase")])
    # downtrend → bullish tech confirms < 2 → weak signal capped at 57
    ev = _run(agent, _ctx(_trending_bars(up=False)))
    assert ev.score <= 57.0


def test_net_selling_is_bearish():
    agent = InsiderAgent()
    _seed(agent, [
        _txn("Rep A", "sale_full"),
        _txn("Rep B", "sale_partial"),
        _txn("Rep C", "sale"),
    ])
    ev = _run(agent, _ctx(_trending_bars(up=False)))  # downtrend confirms bearish
    assert ev.score < 50.0
    assert "congress sellers" in ev.rationale


def test_equal_buyers_and_sellers_is_neutral():
    agent = InsiderAgent()
    _seed(agent, [_txn("Rep A", "purchase"), _txn("Rep B", "sale")])
    ev = _run(agent, _ctx(_trending_bars(up=True)))
    assert ev.score == 50.0
    assert "mixed" in ev.rationale


def test_score_in_valid_range_and_confidence_capped():
    agent = InsiderAgent()
    _seed(agent, [_txn(f"Rep {i}", "purchase", "over $5,000,000") for i in range(8)])
    ev = _run(agent, _ctx(_trending_bars(up=True)))
    assert 1.0 <= ev.score <= 100.0
    assert ev.confidence <= 0.90
