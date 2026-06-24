"""PortfolioManager: composite blending, direction thresholds, kill switch, entry guard."""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from agents.regime_agent import MarketRegime, RegimeSnapshot
from config.settings import Settings
from core.enums import AgentRole, Decision
from core.models import AgentEvaluation
from core.trade_memory import TradeMemory
from execution.portfolio_manager import PortfolioManager

_ET = ZoneInfo("America/New_York")


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


def test_composite_squeeze_boost_on_confirmed_squeeze():
    # A confirmed bullish squeeze (setup == "squeeze_long") gets _SQUEEZE_BOOST
    # extra weight, so it pulls the composite higher than the SAME squeeze score
    # tagged as a non-squeeze setup — all else equal.
    pm = make_pm()
    f = _ev(AgentRole.FUNDAMENTAL, 50)
    v = _ev(AgentRole.VISION, 50)
    t = _ev(AgentRole.TECHNICAL, 50)
    sq_long = AgentEvaluation(
        role=AgentRole.SQUEEZE, score=85, confidence=1.0, data={"setup": "squeeze_long"}
    )
    sq_plain = AgentEvaluation(
        role=AgentRole.SQUEEZE, score=85, confidence=1.0, data={"setup": "moderate_short"}
    )
    boosted = pm._composite(f, v, t, None, None, sq_long)
    plain   = pm._composite(f, v, t, None, None, sq_plain)
    assert boosted > plain


def test_composite_all_none_returns_neutral():
    # When every agent evaluation is None (e.g. all feeds failed), the blender
    # must return the neutral 50.0 rather than raise ZeroDivisionError.
    pm = make_pm()
    assert pm._composite(None, None, None, None) == 50.0


def test_composite_minimum_confidence_all_agents():
    # Clamped minimum confidence (0.05) still produces a valid composite — the
    # denominator is non-zero and the blended score reflects the raw scores.
    pm = make_pm()
    f = _ev(AgentRole.FUNDAMENTAL, 80, confidence=0.01)  # clamped to 0.05
    v = _ev(AgentRole.VISION,      80, confidence=0.01)
    t = _ev(AgentRole.TECHNICAL,   80, confidence=0.01)
    composite = pm._composite(f, v, t, None)
    assert 70.0 < composite < 90.0  # all at 80 → blend near 80


def test_composite_squeeze_boost_applies_at_low_confidence():
    # The SQUEEZE_BOOST is applied before confidence scaling, so even a low-
    # confidence confirmed squeeze still pulls the composite higher than an
    # equally low-confidence non-squeeze setup.
    pm = make_pm()
    f = _ev(AgentRole.FUNDAMENTAL, 50)
    v = _ev(AgentRole.VISION, 50)
    t = _ev(AgentRole.TECHNICAL, 50)
    sq_boosted = AgentEvaluation(
        role=AgentRole.SQUEEZE, score=90, confidence=0.05, data={"setup": "squeeze_long"}
    )
    sq_plain = AgentEvaluation(
        role=AgentRole.SQUEEZE, score=90, confidence=0.05, data={"setup": "moderate"}
    )
    assert pm._composite(f, v, t, None, None, sq_boosted) > pm._composite(f, v, t, None, None, sq_plain)


# ── agent disagreement dispersion ────────────────────────────────────────────

def test_dispersion_zero_when_agents_agree():
    pm = make_pm()
    evals = [_ev(AgentRole.FUNDAMENTAL, 70), _ev(AgentRole.TECHNICAL, 70),
             _ev(AgentRole.VISION, 70)]
    assert pm._directional_dispersion(evals) == pytest.approx(0.0)


def test_dispersion_high_when_agents_conflict():
    pm = make_pm()
    evals = [_ev(AgentRole.FUNDAMENTAL, 85), _ev(AgentRole.TECHNICAL, 15)]
    assert pm._directional_dispersion(evals) == pytest.approx(35.0)


def test_dispersion_excludes_risk_gate():
    pm = make_pm()
    # RISK score (a viability gate, not a direction) must not inflate dispersion.
    evals = [_ev(AgentRole.FUNDAMENTAL, 60), _ev(AgentRole.TECHNICAL, 60),
             _ev(AgentRole.RISK, 5)]
    assert pm._directional_dispersion(evals) == pytest.approx(0.0)


def test_dispersion_zero_with_single_directional_agent():
    pm = make_pm()
    evals = [_ev(AgentRole.TECHNICAL, 80), _ev(AgentRole.RISK, 90)]
    assert pm._directional_dispersion(evals) == 0.0


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


# ── trade protections: intraday peak-drawdown halt ───────────────────────────

def test_intraday_drawdown_halt_catches_giveback():
    pm = make_pm(max_daily_loss_pct=0.03, intraday_drawdown_halt_pct=0.03)
    assert pm._check_daily_loss({"equity": 100_000.0}) is False  # baseline + peak
    assert pm._check_daily_loss({"equity": 104_000.0}) is False  # up 4%, peak=104k
    # back to +0.5%: still fine vs the from-open 3% stop, but >3% below the peak
    assert pm._check_daily_loss({"equity": 100_500.0}) is True
    assert pm._halted


def test_intraday_drawdown_does_not_trip_on_the_way_up():
    pm = make_pm(intraday_drawdown_halt_pct=0.03)
    pm._check_daily_loss({"equity": 100_000.0})
    assert pm._check_daily_loss({"equity": 101_000.0}) is False
    assert pm._check_daily_loss({"equity": 103_000.0}) is False
    assert not pm._halted


# ── trade protections: exit detection (cooldown + loss streak + memory) ──────

def test_observe_positions_sets_reentry_cooldown(tmp_path):
    pm = make_pm(reentry_cooldown_min=15)
    pm._memory = TradeMemory(path=tmp_path / "m.json")
    pm._observe_positions([{"symbol": "NVDA", "unrealized_pl": 20.0}])
    pm._observe_positions([])  # NVDA gone → exited
    assert "NVDA" in pm._cooldown_until
    pm.broker = FakeBroker()
    assert _allowed(pm, "NVDA") is False           # within cooldown
    pm._cooldown_until["NVDA"] = datetime.now(_ET) - timedelta(minutes=1)
    assert _allowed(pm, "NVDA") is True             # cooldown expired


def test_losing_exit_counts_toward_streak_winner_does_not(tmp_path):
    pm = make_pm()
    pm._memory = TradeMemory(path=tmp_path / "m.json")
    pm._observe_positions([{"symbol": "AAPL", "unrealized_pl": 50.0}])
    pm._observe_positions([])                        # AAPL exits a WINNER
    assert pm._recent_stops == []
    pm._observe_positions([{"symbol": "TSLA", "unrealized_pl": -50.0}])
    pm._observe_positions([])                        # TSLA exits a LOSER
    assert len(pm._recent_stops) == 1


def test_stoploss_guard_halts_after_streak(tmp_path):
    pm = make_pm(loss_streak_limit=3, loss_streak_halt_min=60)
    pm._memory = TradeMemory(path=tmp_path / "m.json")
    for sym in ("A", "B", "C"):
        pm._observe_positions([{"symbol": sym, "unrealized_pl": -10.0}])
        pm._observe_positions([])
    assert pm._streak_halt_until is not None
    pm.broker = FakeBroker()
    assert _allowed(pm, "MSFT") is False             # all entries paused


def test_observe_positions_records_outcome_to_memory(tmp_path):
    pm = make_pm()
    pm._memory = TradeMemory(path=tmp_path / "m.json")
    pm._memory.record_decision("NVDA", "LONG", 70.0)
    pm._observe_positions([{"symbol": "NVDA", "unrealized_pl": 120.0}])
    pm._observe_positions([])                        # exit → outcome attached
    entries = pm._memory._load()
    assert entries[0]["outcome_pnl"] == 120.0


# ── trade protections: concentration cap ─────────────────────────────────────

def test_concentration_cap_blocks_correlated_stack():
    pm = make_pm(max_correlated_positions=3)
    pm.broker = FakeBroker(positions=[
        {"symbol": "NVDA"}, {"symbol": "AAPL"}, {"symbol": "MSFT"},  # all mega_tech
    ])
    assert _allowed(pm, "META") is False             # 4th mega-cap tech blocked


def test_concentration_cap_allows_uncorrelated_name():
    pm = make_pm(max_correlated_positions=3)
    pm.broker = FakeBroker(positions=[
        {"symbol": "NVDA"}, {"symbol": "AAPL"}, {"symbol": "MSFT"},
    ])
    assert _allowed(pm, "XOM") is True               # energy name — different group


def test_concentration_cap_disabled_when_zero():
    pm = make_pm(max_correlated_positions=0)
    pm.broker = FakeBroker(positions=[
        {"symbol": "NVDA"}, {"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "AMD"},
    ])
    assert _allowed(pm, "META") is True              # cap off → not blocked
