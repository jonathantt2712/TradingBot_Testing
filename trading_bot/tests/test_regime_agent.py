"""Regime agent: VIX source selection, classify_regime pure logic, and integration."""
import asyncio

import pandas as pd
import pytest

from agents import regime_agent
from agents.regime_agent import MarketRegime, RegimeSnapshot, classify_regime, detect_regime


def _flat_bars(price: float, n: int = 5) -> pd.DataFrame:
    idx = pd.date_range("2026-06-15 09:30", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({
        "open": price, "high": price, "low": price, "close": price, "volume": 1000,
    }, index=idx)


def _rising_bars(start: float, end: float, n: int = 5) -> pd.DataFrame:
    """Bars that close above their session VWAP and up from the open —
    enough to register a single RISK-ON signal for SPY/QQQ."""
    idx = pd.date_range("2026-06-15 09:30", periods=n, freq="5min", tz="UTC")
    closes = [start + (end - start) * i / (n - 1) for i in range(n)]
    return pd.DataFrame({
        "open": start, "high": closes, "low": start, "close": closes, "volume": 1000,
    }, index=idx)


class _FakeBroker:
    """Returns a rising SPY (1 RISK-ON signal), flat QQQ, and a fixed VIXY close."""

    def __init__(self, vixy_close: float):
        self._vixy_close = vixy_close

    async def get_bars(self, symbol, timeframe="5Min", limit=200):
        if symbol == "VIXY":
            return _flat_bars(self._vixy_close)
        if symbol == "SPY":
            return _rising_bars(100.0, 101.0)
        return _flat_bars(100.0)


def test_uses_real_vix_when_available(monkeypatch):
    """VIX=16 (<18) should signal RISK-ON via the real-index thresholds,
    even though VIXY's price (~24) would look RISK-OFF under those cutoffs."""
    async def fake_fetch():
        return 16.0
    monkeypatch.setattr(regime_agent, "_fetch_vix_index", fake_fetch)

    snap = asyncio.run(detect_regime(_FakeBroker(vixy_close=24.0)))

    assert snap.vix_level == 16.0
    assert "VIX=16.0 (<18)" in snap.rationale
    assert "VIXY" not in snap.rationale


def test_falls_back_to_vixy_with_its_own_thresholds(monkeypatch):
    """When the real VIX fetch fails, VIXY's price is used with VIXY-scaled
    thresholds — a VIXY price of 24 should NOT trigger RISK-OFF."""
    async def fake_fetch():
        return 0.0
    monkeypatch.setattr(regime_agent, "_fetch_vix_index", fake_fetch)

    snap = asyncio.run(detect_regime(_FakeBroker(vixy_close=24.0)))

    assert snap.vix_level == 24.0
    assert snap.regime != MarketRegime.RISK_OFF
    assert "VIX=" not in snap.rationale  # no bare "VIX=" (real-index) label


def test_vixy_fallback_risk_off_at_high_price(monkeypatch):
    async def fake_fetch():
        return 0.0
    monkeypatch.setattr(regime_agent, "_fetch_vix_index", fake_fetch)

    snap = asyncio.run(detect_regime(_FakeBroker(vixy_close=40.0)))

    assert snap.regime == MarketRegime.RISK_OFF
    assert "VIXY=40.0 (>38)" in snap.rationale


# ── classify_regime (pure logic) ─────────────────────────────────────────────

def test_classify_risk_off_vix_spike():
    # VIX > 25 alone triggers RISK_OFF regardless of SPY/QQQ
    regime, rationale = classify_regime(
        vix_level=30.0, spy_vs_vwap=1.0, spy_day_chg=0.5,
        qqq_vs_vwap=1.0, qqq_day_chg=0.5,
    )
    assert regime is MarketRegime.RISK_OFF
    assert "VIX=30.0" in rationale


def test_classify_risk_off_spy_selloff():
    # SPY below VWAP AND down >0.8% triggers RISK_OFF even with low VIX
    regime, rationale = classify_regime(
        vix_level=15.0, spy_vs_vwap=-0.5, spy_day_chg=-1.2,
        qqq_vs_vwap=0.5, qqq_day_chg=0.2,
    )
    assert regime is MarketRegime.RISK_OFF
    assert "SPY below VWAP" in rationale


def test_classify_risk_off_qqq_selloff():
    # QQQ below VWAP AND down >0.8% also triggers RISK_OFF
    regime, rationale = classify_regime(
        vix_level=15.0, spy_vs_vwap=0.2, spy_day_chg=0.1,
        qqq_vs_vwap=-1.0, qqq_day_chg=-1.5,
    )
    assert regime is MarketRegime.RISK_OFF


def test_classify_risk_on_requires_two_signals():
    # Low VIX alone (1 signal) is not enough for RISK_ON → NEUTRAL
    regime, _ = classify_regime(
        vix_level=15.0, spy_vs_vwap=None, spy_day_chg=None,
        qqq_vs_vwap=None, qqq_day_chg=None,
    )
    assert regime is MarketRegime.NEUTRAL


def test_classify_risk_on_with_two_signals():
    # Low VIX + SPY above VWAP = 2 RISK-ON signals → RISK_ON
    regime, rationale = classify_regime(
        vix_level=15.0, spy_vs_vwap=0.3, spy_day_chg=0.2,
        qqq_vs_vwap=None, qqq_day_chg=None,
    )
    assert regime is MarketRegime.RISK_ON
    assert "RISK-ON" in rationale


def test_classify_neutral_mid_vix():
    # VIX in neutral band (18-25), SPY slightly above VWAP (1 signal only) → NEUTRAL
    regime, _ = classify_regime(
        vix_level=21.0, spy_vs_vwap=0.2, spy_day_chg=0.1,
        qqq_vs_vwap=None, qqq_day_chg=None,
    )
    assert regime is MarketRegime.NEUTRAL


def test_classify_all_none_is_neutral():
    # No data at all → NEUTRAL
    regime, _ = classify_regime(
        vix_level=None, spy_vs_vwap=None, spy_day_chg=None,
        qqq_vs_vwap=None, qqq_day_chg=None,
    )
    assert regime is MarketRegime.NEUTRAL


# ── RegimeSnapshot threshold deltas ──────────────────────────────────────────

def _snap(regime: MarketRegime) -> RegimeSnapshot:
    return RegimeSnapshot(
        regime=regime, vix_level=None,
        spy_vs_vwap=None, spy_day_chg=None,
        qqq_vs_vwap=None, qqq_day_chg=None,
        rationale="test",
    )


def test_risk_on_loosens_long_tightens_short():
    snap = _snap(MarketRegime.RISK_ON)
    assert snap.long_delta < 0    # lower threshold → easier to go LONG
    assert snap.short_delta > 0   # higher threshold → harder to SHORT


def test_risk_off_tightens_long_loosens_short():
    snap = _snap(MarketRegime.RISK_OFF)
    assert snap.long_delta > 0    # higher threshold → harder to go LONG
    assert snap.short_delta < 0   # lower threshold → easier to SHORT


def test_neutral_no_delta():
    snap = _snap(MarketRegime.NEUTRAL)
    assert snap.long_delta == 0.0
    assert snap.short_delta == 0.0
