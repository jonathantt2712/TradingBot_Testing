"""Market Regime Agent — session-level macro filter for day trading.

Runs ONCE per scan cycle (not per ticker). Outputs a MarketRegime that
PortfolioManager uses to tighten/widen LONG and SHORT thresholds.

Regime logic
------------
RISK_ON   : VIX < 18  AND  SPY + QQQ both above VWAP and up >= 0.1% on day
RISK_OFF  : VIX > 25  OR   SPY below VWAP and down > -0.8% on day
NEUTRAL   : everything else

Threshold adjustments applied by PortfolioManager
--------------------------------------------------
RISK_ON   : LONG threshold  -= 4   (easier to go long)
            SHORT threshold += 4   (harder to short)
RISK_OFF  : LONG threshold  += 8   (harder to go long, protect capital)
            SHORT threshold -= 6   (easier to short)
NEUTRAL   : no change
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── VIX ETF proxy (VIXY) used when broker can't fetch the index itself ─────
_VIX_PROXY = "VIXY"
_SPY = "SPY"
_QQQ = "QQQ"


class MarketRegime(str, Enum):
    RISK_ON  = "risk_on"
    NEUTRAL  = "neutral"
    RISK_OFF = "risk_off"


@dataclass
class RegimeSnapshot:
    regime:         MarketRegime
    vix_level:      Optional[float]   # last close of VIX or VIXY
    spy_vs_vwap:    Optional[float]   # spy price relative to its session VWAP, %
    spy_day_chg:    Optional[float]   # spy % change from open
    qqq_vs_vwap:    Optional[float]
    qqq_day_chg:    Optional[float]
    rationale:      str

    @property
    def reasoning(self) -> dict:
        """Structured explanation for dashboard/audit output."""
        return {
            "regime": self.regime.value,
            "rationale": self.rationale,
            "inputs": {
                "vix": round(self.vix_level, 2) if self.vix_level is not None else None,
                "spy_vs_vwap_pct": round(self.spy_vs_vwap, 3) if self.spy_vs_vwap is not None else None,
                "spy_day_chg_pct": round(self.spy_day_chg, 3) if self.spy_day_chg is not None else None,
                "qqq_vs_vwap_pct": round(self.qqq_vs_vwap, 3) if self.qqq_vs_vwap is not None else None,
                "qqq_day_chg_pct": round(self.qqq_day_chg, 3) if self.qqq_day_chg is not None else None,
            },
            "threshold_shifts": {
                "long_delta": self.long_delta,
                "short_delta": self.short_delta,
                "effect": (
                    "Easier to go long, harder to short"
                    if self.regime is MarketRegime.RISK_ON else
                    "Harder to go long, easier to short"
                    if self.regime is MarketRegime.RISK_OFF else
                    "No threshold adjustment"
                ),
            },
            "rules": {
                "risk_on": "VIX < 18 AND SPY + QQQ both above VWAP and up ≥ 0.1%",
                "risk_off": "VIX > 25 OR SPY below VWAP and down > 0.8%",
                "neutral": "All other conditions",
            },
        }

    # Threshold deltas to apply (signed integers)
    @property
    def long_delta(self) -> float:
        return {MarketRegime.RISK_ON: -4.0, MarketRegime.NEUTRAL: 0.0, MarketRegime.RISK_OFF: 8.0}[self.regime]

    @property
    def short_delta(self) -> float:
        return {MarketRegime.RISK_ON: 4.0, MarketRegime.NEUTRAL: 0.0, MarketRegime.RISK_OFF: -6.0}[self.regime]


def _session_vwap(df: pd.DataFrame) -> float:
    """Compute VWAP for the current session (same date as last bar)."""
    today = df.index[-1].date()
    today_df = df[df.index.date == today]
    if today_df.empty:
        today_df = df.tail(78)          # fallback: last 78 × 5-min bars ≈ 1 day
    typical = (today_df["high"] + today_df["low"] + today_df["close"]) / 3
    cumvol = today_df["volume"].cumsum()
    cumtpv = (typical * today_df["volume"]).cumsum()
    return float((cumtpv / cumvol).iloc[-1])


def _day_change_pct(df: pd.DataFrame) -> float:
    """% change from today's open bar to latest close."""
    today = df.index[-1].date()
    today_df = df[df.index.date == today]
    if today_df.empty:
        today_df = df.tail(78)
    open_price = float(today_df["open"].iloc[0])
    last_price = float(today_df["close"].iloc[-1])
    return (last_price - open_price) / open_price * 100


async def detect_regime(broker) -> RegimeSnapshot:
    """Fetch SPY, QQQ, and VIX data concurrently and return a RegimeSnapshot."""
    try:
        spy_task = broker.get_bars(_SPY,  timeframe="5Min", limit=100)
        qqq_task = broker.get_bars(_QQQ,  timeframe="5Min", limit=100)
        vix_task = broker.get_bars(_VIX_PROXY, timeframe="1Day", limit=5)

        spy_bars, qqq_bars, vix_bars = await asyncio.gather(
            spy_task, qqq_task, vix_task, return_exceptions=True
        )
    except Exception as exc:
        logger.warning("regime: fetch error — defaulting to NEUTRAL: %s", exc)
        return RegimeSnapshot(
            regime=MarketRegime.NEUTRAL, vix_level=None,
            spy_vs_vwap=None, spy_day_chg=None,
            qqq_vs_vwap=None, qqq_day_chg=None,
            rationale="data unavailable — defaulting to NEUTRAL",
        )

    # ── VIX proxy ──────────────────────────────────────────────────────────
    vix_level: Optional[float] = None
    if isinstance(vix_bars, pd.DataFrame) and not vix_bars.empty:
        vix_level = float(vix_bars["close"].iloc[-1])

    # ── SPY ────────────────────────────────────────────────────────────────
    spy_vs_vwap: Optional[float] = None
    spy_day_chg: Optional[float] = None
    if isinstance(spy_bars, pd.DataFrame) and not spy_bars.empty:
        vwap = _session_vwap(spy_bars)
        last = float(spy_bars["close"].iloc[-1])
        spy_vs_vwap = (last - vwap) / vwap * 100
        spy_day_chg = _day_change_pct(spy_bars)

    # ── QQQ ────────────────────────────────────────────────────────────────
    qqq_vs_vwap: Optional[float] = None
    qqq_day_chg: Optional[float] = None
    if isinstance(qqq_bars, pd.DataFrame) and not qqq_bars.empty:
        vwap = _session_vwap(qqq_bars)
        last = float(qqq_bars["close"].iloc[-1])
        qqq_vs_vwap = (last - vwap) / vwap * 100
        qqq_day_chg = _day_change_pct(qqq_bars)

    # ── Classify regime ────────────────────────────────────────────────────
    risk_off_triggers = []
    risk_on_signals  = []

    if vix_level is not None:
        if vix_level > 25:
            risk_off_triggers.append(f"VIX={vix_level:.1f} (>25)")
        elif vix_level < 18:
            risk_on_signals.append(f"VIX={vix_level:.1f} (<18)")

    if spy_vs_vwap is not None:
        if spy_vs_vwap < 0 and spy_day_chg is not None and spy_day_chg < -0.8:
            risk_off_triggers.append(f"SPY below VWAP {spy_vs_vwap:.2f}% / day {spy_day_chg:.2f}%")
        elif spy_vs_vwap > 0 and spy_day_chg is not None and spy_day_chg >= 0.1:
            risk_on_signals.append(f"SPY above VWAP {spy_vs_vwap:.2f}%")

    if qqq_vs_vwap is not None:
        if qqq_vs_vwap < 0 and qqq_day_chg is not None and qqq_day_chg < -0.8:
            risk_off_triggers.append(f"QQQ below VWAP {qqq_vs_vwap:.2f}% / day {qqq_day_chg:.2f}%")
        elif qqq_vs_vwap > 0 and qqq_day_chg is not None and qqq_day_chg >= 0.1:
            risk_on_signals.append(f"QQQ above VWAP {qqq_vs_vwap:.2f}%")

    if risk_off_triggers:
        regime = MarketRegime.RISK_OFF
        rationale = "RISK-OFF: " + "; ".join(risk_off_triggers)
    elif len(risk_on_signals) >= 2:
        regime = MarketRegime.RISK_ON
        rationale = "RISK-ON: " + "; ".join(risk_on_signals)
    else:
        regime = MarketRegime.NEUTRAL
        rationale = "NEUTRAL: mixed signals"

    snap = RegimeSnapshot(
        regime=regime, vix_level=vix_level,
        spy_vs_vwap=spy_vs_vwap, spy_day_chg=spy_day_chg,
        qqq_vs_vwap=qqq_vs_vwap, qqq_day_chg=qqq_day_chg,
        rationale=rationale,
    )
    logger.info("REGIME: %s | %s", regime.value.upper(), rationale)
    return snap
