"""Regime agent: VIX source selection (real index vs VIXY proxy)."""
import asyncio

import pandas as pd
import pytest

from agents import regime_agent
from agents.regime_agent import MarketRegime, detect_regime


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
