"""RiskAgent: fail-closed sizing and structure-based R/R."""
import asyncio

import pytest

from agents.risk_agent import RiskAgent
from config.settings import RiskConfig
from core.enums import Decision
from core.models import AnalysisContext

from conftest import make_session_bars


def _cfg() -> RiskConfig:
    cfg = RiskConfig()
    cfg.max_risk_per_trade_pct = 0.01
    cfg.min_risk_reward        = 1.5
    cfg.max_position_pct       = 0.20
    cfg.atr_stop_multiple      = 2.0
    cfg.atr_target_multiple    = 3.0
    return cfg


def test_no_equity_refuses_to_plan(flat_bars):
    agent = RiskAgent(_cfg())
    ctx = AnalysisContext(ticker="TEST", bars=flat_bars, account={})
    assert agent.build_plan(ctx, intended=Decision.LONG) is None


def test_zero_equity_refuses_to_plan(flat_bars):
    agent = RiskAgent(_cfg())
    ctx = AnalysisContext(ticker="TEST", bars=flat_bars, account={"equity": 0.0})
    assert agent.build_plan(ctx, intended=Decision.LONG) is None


def test_sizing_respects_exposure_cap():
    # Breakout shape so the structure cap doesn't zero the target.
    bars = make_session_bars([100.0] * 29 + [101.0])
    bars.loc[bars.index[-1], "high"] = 101.0   # close == session high

    agent = RiskAgent(_cfg())
    ctx = AnalysisContext(ticker="TEST", bars=bars, account={"equity": 50_000.0})
    plan = agent.build_plan(ctx, intended=Decision.LONG)
    assert plan is not None
    # Risk cap allows ~$500 / ~$2-stop ≈ 240+ shares, but the 20% exposure cap
    # (50k * 0.2 / ~$101 ≈ 99 shares) must bind.
    assert plan.qty <= 100
    assert plan.qty >= 90


def test_rr_capped_by_overhead_structure():
    # Session high spiked to 101.5 early on; price 100, ATR ≈ 1.
    # Room ≈ 1.5 < ATR target 3.0 → target capped → R/R 0.75 instead of 1.5.
    bars = make_session_bars([100.0] * 40)
    bars.loc[bars.index[10], "high"] = 101.5

    agent = RiskAgent(_cfg())
    ctx = AnalysisContext(ticker="TEST", bars=bars, account={"equity": 100_000.0})
    plan = agent.build_plan(ctx, intended=Decision.LONG)
    assert plan is not None
    assert plan.risk_reward == pytest.approx(0.75, rel=0.05)


def test_rr_full_atr_target_on_breakout():
    # Close at the session high → no overhead structure → full ATR target,
    # so R/R equals target_multiple / stop_multiple.
    bars = make_session_bars([100.0] * 29 + [101.0])
    bars.loc[bars.index[-1], "high"] = 101.0

    agent = RiskAgent(_cfg())
    ctx = AnalysisContext(ticker="TEST", bars=bars, account={"equity": 100_000.0})
    plan = agent.build_plan(ctx, intended=Decision.LONG)
    assert plan is not None
    assert plan.risk_reward == pytest.approx(1.5, rel=0.01)


def test_evaluate_vetoes_when_no_room(flat_bars):
    # Flat session: price is mid-range with ~0.5 room either way against a
    # ~2.0 stop → R/R ≈ 0.25 for both directions → veto.
    # backtest_mode skips the freshness veto so this exercises the R/R path.
    agent = RiskAgent(_cfg())
    ctx = AnalysisContext(ticker="TEST", bars=flat_bars,
                          account={"equity": 100_000.0}, backtest_mode=True)
    ev = asyncio.run(agent.evaluate(ctx))
    assert ev.veto
