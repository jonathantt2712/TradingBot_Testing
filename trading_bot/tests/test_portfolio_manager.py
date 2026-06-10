"""PortfolioManager: composite blending, direction thresholds, kill switch, entry guard."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agents.regime_agent import MarketRegime, RegimeSnapshot
from config.settings import Settings
from core.enums import AgentRole, Decision
from core.models import AgentEvaluation
from execution.portfolio_manager import PortfolioManager


def _ev(role: AgentRole, score: float, confidence: float = 1.0) -> AgentEvaluation:
    return AgentEvaluation(role=role, score=score, confidence=confidence)


def make_pm(**risk_overrides) -> PortfolioManager:
    settings = Settings()
    for key, val in risk_overrides.items():
        setattr(settings.risk, key, val)
    return PortfolioManager(
        settings=settings,
        broker=None,
        fundamental=None,
        technical=None,
        risk=None,
    )


def _regime(kind: MarketRegime) -> RegimeSnapshot:
    return RegimeSnapshot(
        regime=kind, vix_level=None,
        spy_vs_vwap=None, spy_day_chg=None,
        qqq_vs_vwap=None, qqq_day_chg=None,
        rationale="test",
    )


# ── composite ────────────────────────────────────────────────────────────────

def test_composite_equal_scores_pass_through():
    pm = make_pm()
    f = _ev(AgentRole.FUNDAMENTAL, 70)
    v = _ev(AgentRole.VISION, 70)
    t = _ev(AgentRole.TECHNICAL, 70)
    assert pm._composite(f, v, t, None, None) == pytest.approx(70.0)


def test_composite_weights_technical_heaviest():
    pm = make_pm()  # default weights: technical 0.35 vs fundamental 0.20
    f = _ev(AgentRole.FUNDAMENTAL, 20)
    v = _ev(AgentRole.VISION, 50)
    t = _ev(AgentRole.TECHNICAL, 80)
    composite = pm._composite(f, v, t, None, None)
    assert composite > 50.0  # technical pulls harder than fundamental


def test_composite_confidence_scales_weight():
    pm = make_pm()
    f = _ev(AgentRole.FUNDAMENTAL, 90, confidence=0.05)  # barely trusted
    v = _ev(AgentRole.VISION, 50, confidence=1.0)
    t = _ev(AgentRole.TECHNICAL, 50, confidence=1.0)
    composite = pm._composite(f, v, t, None, None)
    assert composite < 60.0


# ── direction thresholds ─────────────────────────────────────────────────────

def test_direction_defaults():
    pm = make_pm()
    assert pm._direction(65.0) is Decision.LONG
    assert pm._direction(35.0) is Decision.SHORT
    assert pm._direction(50.0) is Decision.PASS


def test_direction_retail_surcharge_raises_bar():
    pm = make_pm()
    assert pm._direction(62.0) is Decision.LONG
    assert pm._direction(62.0, retail_surcharge=5.0) is Decision.PASS


def test_direction_risk_off_regime_shifts_both_bars():
    pm = make_pm()
    pm.set_regime(_regime(MarketRegime.RISK_OFF))
    assert pm._direction(65.0) is Decision.PASS       # long bar: 60 + 8 = 68
    assert pm._direction(69.0) is Decision.LONG
    assert pm._direction(35.0) is Decision.PASS       # short bar: 40 - 6 = 34
    assert pm._direction(33.0) is Decision.SHORT


# ── daily-loss kill switch ───────────────────────────────────────────────────

def test_kill_switch_trips_after_daily_loss():
    pm = make_pm(max_daily_loss_pct=0.03)
    assert pm._check_daily_loss({"equity": 100_000.0}) is False   # baseline set
    assert pm._check_daily_loss({"equity": 98_000.0}) is False    # -2%: fine
    assert pm._check_daily_loss({"equity": 96_500.0}) is True     # -3.5%: halt
    assert pm._halted
    # stays halted for the rest of the day even if equity recovers
    assert pm._check_daily_loss({"equity": 99_000.0}) is True


def test_kill_switch_ignores_unknown_equity():
    pm = make_pm(max_daily_loss_pct=0.03)
    pm._check_daily_loss({"equity": 100_000.0})
    assert pm._check_daily_loss({}) is False          # unknown equity: no change
    assert pm._check_daily_loss({"equity": 0}) is False


def test_kill_switch_resets_next_day():
    pm = make_pm(max_daily_loss_pct=0.03)
    pm._check_daily_loss({"equity": 100_000.0})
    pm._check_daily_loss({"equity": 90_000.0})
    assert pm._halted
    pm._kill_switch_date = None                       # simulate date rollover
    assert pm._check_daily_loss({"equity": 90_000.0}) is False
    assert not pm._halted


# ── entry guard ──────────────────────────────────────────────────────────────

class FakeBroker:
    def __init__(self, positions=None, orders=None, fail=False):
        self._positions = positions or []
        self._orders = orders or []
        self._fail = fail

    async def get_positions(self):
        if self._fail:
            raise RuntimeError("api down")
        return self._positions

    async def get_open_orders(self):
        if self._fail:
            raise RuntimeError("api down")
        return self._orders


def _allowed(pm, ticker):
    return asyncio.run(pm._entry_allowed(ticker))


def test_entry_blocked_when_position_open():
    pm = make_pm()
    pm.broker = FakeBroker(positions=[{"symbol": "NVDA", "qty": 10}])
    assert _allowed(pm, "NVDA") is False
    assert _allowed(pm, "AAPL") is True


def test_entry_blocked_when_order_working():
    pm = make_pm()
    pm.broker = FakeBroker(orders=[{"symbol": "NVDA"}])
    assert _allowed(pm, "nvda") is False


def test_entry_blocked_at_max_positions():
    pm = make_pm(max_open_positions=2)
    pm.broker = FakeBroker(positions=[{"symbol": "A", "qty": 1}, {"symbol": "B", "qty": 1}])
    assert _allowed(pm, "C") is False


def test_entry_blocked_when_state_unavailable():
    pm = make_pm()
    pm.broker = FakeBroker(fail=True)
    assert _allowed(pm, "NVDA") is False
