"""TechnicalAgent: ORB scoring and day-change helper."""
import numpy as np
import pytest

from agents.technical_agent import TechnicalAgent, _day_change_pct

from conftest import make_session_bars


def make_agent() -> TechnicalAgent:
    return TechnicalAgent(weight=0.35)


def test_orb_breakout_above():
    # Opening range (first 3 bars) ~99.5-100.5; later close at 103 → bullish.
    bars = make_session_bars([100.0] * 3 + [101.0, 102.0, 103.0])
    score = make_agent()._orb_score(bars)
    assert score is not None
    assert 65 <= score <= 90


def test_orb_breakdown_below():
    bars = make_session_bars([100.0] * 3 + [99.0, 98.0, 97.0])
    score = make_agent()._orb_score(bars)
    assert score is not None
    assert 10 <= score <= 35


def test_orb_inside_range_is_neutral():
    bars = make_session_bars([100.0] * 8)
    score = make_agent()._orb_score(bars)
    assert score is not None
    assert 45 <= score <= 55


def test_orb_needs_followthrough_bars():
    # Opening range bars only — no confirmation yet → None.
    bars = make_session_bars([100.0] * 4)
    assert make_agent()._orb_score(bars) is None


def test_day_change_pct():
    bars = make_session_bars([100.0] * 5 + [102.0] * 5)
    assert _day_change_pct(bars) == pytest.approx(2.0, rel=0.01)


def test_day_range_position_at_high():
    bars = make_session_bars([100.0, 101.0, 102.0, 103.0, 104.0])
    pos = make_agent()._day_range_position(bars)
    assert pos is not None
    assert pos > 0.8
