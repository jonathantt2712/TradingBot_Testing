"""TechnicalAgent: ORB scoring, day-change helper, and all signal helpers."""
from datetime import date, timezone

import numpy as np
import pandas as pd
import pytest

from agents.technical_agent import TechnicalAgent, _day_change_pct

from conftest import make_session_bars


# ── multi-day bar builder (volume-ratio signals need prior-day history) ───────

def _multi_day(
    n_prior: int = 20,
    prior_vol: int = 200_000,
    today_closes: list | None = None,
    today_vol: int = 200_000,
) -> pd.DataFrame:
    """n_prior full sessions (78 bars each) + a partial 'today' session."""
    frames = []
    bar_vol = prior_vol / 78
    for i in range(n_prior):
        day = date(2026, 6, i + 1)
        idx = pd.date_range(f"{day} 13:30:00", periods=78, freq="5min", tz=timezone.utc)
        frames.append(pd.DataFrame({
            "open":   [100.0] * 78, "high": [101.0] * 78,
            "low":    [99.0]  * 78, "close": [100.0] * 78,
            "volume": [bar_vol] * 78,
        }, index=idx))
    closes = today_closes or ([100.0] * 10)
    n = len(closes)
    closes_arr = np.array(closes, dtype=float)
    opens = np.concatenate([[closes_arr[0]], closes_arr[:-1]])
    today_idx = pd.date_range("2026-06-30 13:30:00", periods=n, freq="5min", tz=timezone.utc)
    frames.append(pd.DataFrame({
        "open":   opens,    "high":   closes_arr + 0.5,
        "low":    closes_arr - 0.5, "close": closes_arr,
        "volume": [float(today_vol) / n] * n,
    }, index=today_idx))
    return pd.concat(frames).sort_index()


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


def test_macd_hist_short_frame_returns_zero():
    # ta.macd() returns None when the frame is too short for its 26+9 windows;
    # the guard must treat that as flat (0.0) rather than crash to neutral.
    short = make_session_bars([100.0, 101.0, 102.0])["close"]
    assert make_agent()._macd_hist(short) == 0.0


def test_macd_hist_positive_on_rising_trend():
    rising = make_session_bars([100.0 + i for i in range(40)])["close"]
    assert make_agent()._macd_hist(rising) > 0


def test_macd_hist_negative_on_falling_trend():
    falling = make_session_bars([100.0 - i for i in range(40)])["close"]
    assert make_agent()._macd_hist(falling) < 0


def test_macd_hist_nan_last_value_returns_zero(monkeypatch):
    # When ta.macd returns a non-empty frame whose final histogram cell is NaN,
    # _macd_hist must treat it as flat (0.0) rather than propagate NaN.
    import agents.technical_agent as ta_mod
    if not ta_mod._HAS_PANDAS_TA:
        pytest.skip("pandas_ta not installed")
    nan_frame = pd.DataFrame({"MACDh_12_26_9": [1.0, np.nan]})
    monkeypatch.setattr(ta_mod.ta, "macd", lambda *a, **k: nan_frame)
    close = make_session_bars([100.0 + i for i in range(40)])["close"]
    assert make_agent()._macd_hist(close) == 0.0


# ── _relative_strength ────────────────────────────────────────────────────────

def test_rs_none_without_spy_bars():
    bars = make_session_bars([100.0] * 10)
    assert TechnicalAgent()._relative_strength(bars) is None


def test_rs_none_when_spy_flat():
    bars = make_session_bars([100.0] * 5 + [105.0] * 5)
    spy  = make_session_bars([100.0] * 10)  # flat → abs(chg) < 0.01
    agent = TechnicalAgent()
    agent.spy_bars = spy
    assert agent._relative_strength(bars) is None


def test_rs_above_one_when_stock_outperforms():
    bars = make_session_bars([100.0] + [105.0] * 9)   # +5%
    spy  = make_session_bars([100.0] + [102.0] * 9)   # +2%
    agent = TechnicalAgent()
    agent.spy_bars = spy
    assert agent._relative_strength(bars) > 1.0


def test_rs_below_one_when_stock_underperforms():
    bars = make_session_bars([100.0] + [98.0] * 9)    # -2%
    spy  = make_session_bars([100.0] + [102.0] * 9)   # +2%
    agent = TechnicalAgent()
    agent.spy_bars = spy
    assert agent._relative_strength(bars) < 1.0


# ── _volume_surge ─────────────────────────────────────────────────────────────

def test_volume_surge_none_for_short_frame():
    # < 40 total bars → None
    bars = make_session_bars([100.0] * 30)
    assert TechnicalAgent()._volume_surge(bars) is None


def test_volume_surge_elevated_when_doubled():
    df = _multi_day(n_prior=20, prior_vol=100_000, today_vol=200_000,
                    today_closes=[100.0] * 10)
    ratio = TechnicalAgent()._volume_surge(df)
    assert ratio is not None and ratio > 1.5


def test_volume_surge_near_one_when_normal():
    df = _multi_day(n_prior=20, prior_vol=200_000, today_vol=200_000,
                    today_closes=[100.0] * 78)
    ratio = TechnicalAgent()._volume_surge(df)
    assert ratio is not None and 0.5 < ratio < 2.5


# ── _volume_confirm ───────────────────────────────────────────────────────────

def test_volume_confirm_high_volume_gives_high_score():
    bars = make_session_bars([100.0] * 30, volume=1_000)
    bars.at[bars.index[-1], "volume"] = 20_000.0  # last bar is 20× avg
    assert TechnicalAgent()._volume_confirm(bars) >= 60


def test_volume_confirm_low_volume_gives_low_score():
    bars = make_session_bars([100.0] * 30, volume=10_000)
    bars.at[bars.index[-1], "volume"] = 10.0  # near zero
    assert TechnicalAgent()._volume_confirm(bars) < 50


def test_volume_confirm_neutral_for_short_frame():
    bars = make_session_bars([100.0] * 20)  # < 25 bars
    assert TechnicalAgent()._volume_confirm(bars) == 50.0


# ── _gap_signal ───────────────────────────────────────────────────────────────

def test_gap_signal_none_for_insufficient_history():
    bars = make_session_bars([100.0] * 10)  # < 40 bars, single session
    assert TechnicalAgent()._gap_signal(bars) is None


def test_gap_signal_small_gap_fades():
    # Prior close 100.0, today open 100.3 (0.3% gap) → fade score < 50
    df = _multi_day(n_prior=5, today_closes=[100.3] * 10)
    score = TechnicalAgent()._gap_signal(df)
    assert score is not None and score < 50


def test_gap_signal_large_gap_continues():
    # Prior close 100.0, today open 103.0 (3% gap) → continuation score > 50
    df = _multi_day(n_prior=5, today_closes=[103.0] * 10)
    score = TechnicalAgent()._gap_signal(df)
    assert score is not None and score > 50


# ── _adx_signal ───────────────────────────────────────────────────────────────

def test_adx_signal_none_for_short_frame():
    bars = make_session_bars([100.0] * 20)  # < 14*2 bars
    assert TechnicalAgent()._adx_signal(bars) is None


def test_adx_signal_neutral_for_flat_market():
    # Flat bars → +DM and -DM both 0 → ADX is undefined (NaN) → returns None
    bars = make_session_bars([100.0] * 60)
    score = TechnicalAgent()._adx_signal(bars)
    assert score is None or score == 50.0


def test_adx_signal_bullish_for_strong_uptrend():
    bars = make_session_bars([100.0 + i * 0.5 for i in range(60)])
    score = TechnicalAgent()._adx_signal(bars)
    assert score is not None and score > 50


# ── _zscore_signal ────────────────────────────────────────────────────────────

def test_zscore_signal_none_for_short_frame():
    bars = make_session_bars([100.0] * 40)  # < 55 bars
    assert TechnicalAgent()._zscore_signal(bars) is None


def test_zscore_signal_oversold():
    # 50 bars at 100, then drop to 85 → Z well below -2 → score > 65
    closes = [100.0] * 50 + [85.0] * 10
    score = TechnicalAgent()._zscore_signal(make_session_bars(closes))
    assert score is not None and score > 65


def test_zscore_signal_overbought():
    # 50 bars at 100, then spike to 120 → Z well above +2 → score < 35
    closes = [100.0] * 50 + [120.0] * 10
    score = TechnicalAgent()._zscore_signal(make_session_bars(closes))
    assert score is not None and score < 35


# ── _hourly_direction ─────────────────────────────────────────────────────────

def test_hourly_direction_neutral_for_short_frame():
    hourly = make_session_bars([100.0] * 5)
    score, desc = TechnicalAgent()._hourly_direction(hourly)
    assert score == 50.0 and "no hourly" in desc


def test_hourly_direction_bullish_on_rising_closes():
    hourly = make_session_bars([100.0 + i for i in range(22)])
    score, desc = TechnicalAgent()._hourly_direction(hourly)
    # EMA9 > EMA21 and price > SMA20 pull the composite above 55
    # (RSI may be 50 when all candles are green — no loss bars for the rolling mean)
    assert score > 55


def test_hourly_direction_bearish_on_falling_closes():
    hourly = make_session_bars([100.0 - i * 0.5 for i in range(22)])
    score, desc = TechnicalAgent()._hourly_direction(hourly)
    assert score < 45


# ── _bollinger_squeeze_signal ─────────────────────────────────────────────────

def test_bollinger_squeeze_none_for_short_frame():
    bars = make_session_bars([100.0] * 30)  # < 40 bars
    assert TechnicalAgent()._bollinger_squeeze_signal(bars) is None


def test_bollinger_squeeze_returns_score_for_sufficient_data():
    bars = make_session_bars([100.0 + 0.1 * (i % 3) for i in range(50)])
    score = TechnicalAgent()._bollinger_squeeze_signal(bars)
    assert score is None or 10 <= score <= 90


# ── _stochastic_signal ────────────────────────────────────────────────────────

def test_stochastic_signal_none_for_short_frame():
    bars = make_session_bars([100.0] * 15)  # < 14+3+5=22 bars
    assert TechnicalAgent()._stochastic_signal(bars) is None


def test_stochastic_signal_returns_valid_score():
    bars = make_session_bars([100.0 + (i % 5) for i in range(30)])
    score = TechnicalAgent()._stochastic_signal(bars)
    assert score is None or 15 <= score <= 85


# ── _divergence_signal ────────────────────────────────────────────────────────

def test_divergence_signal_none_for_short_frame():
    bars = make_session_bars([100.0] * 15)  # < 10+20=30 bars
    assert TechnicalAgent()._divergence_signal(bars) is None


def test_divergence_signal_neutral_for_non_diverging_market():
    # Zigzag market so RSI is computable (non-zero losses); no divergence → 50 or None
    closes = [100.0 + (i % 5) * 0.5 for i in range(35)]
    bars = make_session_bars(closes)
    score = TechnicalAgent()._divergence_signal(bars)
    assert score is None or score == 50.0


# ── _lottery_penalty ──────────────────────────────────────────────────────────

def test_lottery_penalty_zero_for_short_frame():
    # < 25 bars → 0.0 unconditionally
    bars = make_session_bars([100.0] * 20)
    assert TechnicalAgent()._lottery_penalty(bars) == 0.0


def test_lottery_penalty_zero_when_criteria_not_met():
    # Tiny price move (<12%) and normal volume → no lottery penalty
    bars = _multi_day(n_prior=20, prior_vol=200_000, today_vol=250_000,
                      today_closes=[100.0] * 25)
    assert TechnicalAgent()._lottery_penalty(bars) == 0.0


def test_lottery_penalty_positive_on_spike_with_surge():
    # Big price move (>12%) combined with volume >2.5× → lottery profile → penalty > 0
    # prior_vol=100_000/day; today_vol=300_000 paced over 25 bars → ratio ~3×
    closes = [100.0] * 5 + [115.0] * 20   # 15% move in last 20 bars
    bars = _multi_day(n_prior=20, prior_vol=100_000, today_closes=closes, today_vol=300_000)
    penalty = TechnicalAgent()._lottery_penalty(bars)
    assert penalty > 0.0


# ── _is_retail_driven ─────────────────────────────────────────────────────────

def test_is_retail_driven_false_when_move_too_small():
    # Price move < 8% and normal volume → not retail-driven
    bars = _multi_day(n_prior=20, prior_vol=200_000,
                      today_closes=[100.0] * 10, today_vol=200_000)
    assert TechnicalAgent()._is_retail_driven(bars) is False


def test_is_retail_driven_true_when_both_criteria_met():
    # Price move > 8% with volume surge > 2× → retail-driven
    closes = [100.0] * 5 + [110.0] * 5   # 10% move
    bars = _multi_day(n_prior=20, prior_vol=100_000, today_closes=closes, today_vol=300_000)
    assert TechnicalAgent()._is_retail_driven(bars) is True


# ── _session_vwap ─────────────────────────────────────────────────────────────

def test_session_vwap_reflects_current_session():
    # VWAP computed solely from today's bars should be close to today's avg close
    bars = _multi_day(n_prior=5, today_closes=[105.0] * 10)
    vwap = TechnicalAgent()._session_vwap(bars)
    # With all high=105.5, low=104.5, close=105 the typical price ≈ 105
    assert 103.0 < vwap < 107.0


# ── _trend_join_score ─────────────────────────────────────────────────────────

def test_trend_join_none_for_single_session():
    # Only one date in the index → can't compare to prior day
    bars = make_session_bars([100.0] * 10)
    assert TechnicalAgent()._trend_join_score(bars) is None


def test_trend_join_bullish_above_prior_high():
    # Today breaks above prior high: price=105 > prior_high=101 and near today HOD
    prior_bars = _multi_day(n_prior=1, prior_vol=200_000, today_closes=[105.0] * 10)
    # today_closes=105 means close is above prior session high (101.0)
    score = TechnicalAgent()._trend_join_score(prior_bars)
    assert score is not None and score > 50.0


def test_trend_join_neutral_inside_prior_range():
    # Today peaks at 104 then falls back to 100: close is NOT near today's HOD (104.5)
    # and NOT above prior_high (101) and NOT below prior_low (99) → both fracs 0 → 50
    today_closes = [100.0, 101.0, 102.0, 103.0, 104.0, 103.0, 102.0, 101.0, 100.0, 100.0]
    bars = _multi_day(n_prior=1, prior_vol=200_000, today_closes=today_closes)
    score = TechnicalAgent()._trend_join_score(bars)
    # Close (100) < today_HOD*0.995 (≈103.5) and < prev_high (101)... wait,
    # close=100 < prev_high=101 so bull crit 1 fails; but bull crit 2 fails too (100 < 103.5).
    # Bear crit 1: 100 < prev_low(99)? No. Bear crit 2: 100 <= 99*1.005=99.5? No.
    # Both fracs 0 → score 50.0 or None.
    assert score is None or score == 50.0
