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


def test_zero_atr_refuses_to_plan():
    # Truly flat bars (all identical OHLCV) produce ATR = 0; build_plan must
    # return None rather than divide by zero in sizing.
    bars = make_session_bars([100.0] * 40, bar_range=0.0)
    agent = RiskAgent(_cfg())
    ctx = AnalysisContext(ticker="TEST", bars=bars, account={"equity": 100_000.0})
    assert agent.build_plan(ctx, intended=Decision.LONG) is None


def test_volatility_multiplier_clips_high_volatility():
    # ATR = 10 on a $100 stock → atr_pct = 10% >> 1.5% baseline → mult < 1.
    # The 0.5x floor prevents position size from going to near-zero.
    mult = RiskAgent._volatility_multiplier(atr=10.0, price=100.0)
    assert mult == pytest.approx(0.5)  # floor: 0.015 / 0.10 = 0.15, clipped to 0.5


def test_volatility_multiplier_clips_low_volatility():
    # ATR = 0.1 on a $100 stock → atr_pct = 0.1% << 1.5% baseline → mult > 1.
    # The 1.5x ceiling prevents position over-sizing on illiquid, penny-like spread.
    mult = RiskAgent._volatility_multiplier(atr=0.1, price=100.0)
    assert mult == pytest.approx(1.5)  # ceiling: 0.015 / 0.001 = 15, clipped to 1.5


def test_volatility_multiplier_neutral_at_baseline():
    # ATR = 1.5 on a $100 stock → atr_pct = 1.5% = baseline → exactly 1.0x.
    mult = RiskAgent._volatility_multiplier(atr=1.5, price=100.0)
    assert mult == pytest.approx(1.0, rel=1e-6)


def test_kelly_multiplier_backtest_mode_returns_one():
    # In backtest mode Kelly must return 1.0 unconditionally (no file read,
    # no future-data leak into historical sizing).
    assert RiskAgent._kelly_multiplier(rr=2.0, backtest_mode=True) == 1.0


def test_kelly_multiplier_missing_file_returns_one(tmp_path, monkeypatch):
    # When strategy_weights.json doesn't exist the exception handler must return 1.0
    # rather than crashing and zero-sizing every trade.
    import agents.risk_agent as ra_mod
    monkeypatch.setattr(ra_mod, "_STRATEGY_WEIGHTS_FILE", tmp_path / "nonexistent.json")
    assert RiskAgent._kelly_multiplier(rr=2.0) == pytest.approx(1.0)


def test_kelly_multiplier_negative_kelly_returns_quarter(tmp_path, monkeypatch):
    # Win rate 40%, R/R 1.0 → K = 0.4 - 0.6 = -0.2 → negative Kelly → 0.25x.
    import json as _json
    import agents.risk_agent as ra_mod
    weights_file = tmp_path / "strategy_weights.json"
    weights_file.write_text(_json.dumps({"win_rate_30d": 40.0, "update_count": 50}))
    monkeypatch.setattr(ra_mod, "_STRATEGY_WEIGHTS_FILE", weights_file)
    assert RiskAgent._kelly_multiplier(rr=1.0) == pytest.approx(0.25)


def test_kelly_multiplier_positive_kelly_scales_up(tmp_path, monkeypatch):
    # Win rate 60%, R/R 2.0 → K = 0.4; K_neutral = 0.25 → 1.6x (clamped to 2.0x max).
    import json as _json
    import agents.risk_agent as ra_mod
    weights_file = tmp_path / "strategy_weights.json"
    weights_file.write_text(_json.dumps({"win_rate_30d": 60.0, "update_count": 50}))
    monkeypatch.setattr(ra_mod, "_STRATEGY_WEIGHTS_FILE", weights_file)
    mult = RiskAgent._kelly_multiplier(rr=2.0)
    assert mult == pytest.approx(1.6, rel=0.01)


def test_kelly_multiplier_capped_when_few_trades(tmp_path, monkeypatch):
    # With fewer than 30 trades, kelly_mult is capped at 1.0x even when win-rate
    # is high enough to normally push it above 1.0x.
    import json as _json
    import agents.risk_agent as ra_mod
    weights_file = tmp_path / "strategy_weights.json"
    weights_file.write_text(_json.dumps({"win_rate_30d": 70.0, "update_count": 10}))
    monkeypatch.setattr(ra_mod, "_STRATEGY_WEIGHTS_FILE", weights_file)
    mult = RiskAgent._kelly_multiplier(rr=2.0)
    assert mult <= 1.0
