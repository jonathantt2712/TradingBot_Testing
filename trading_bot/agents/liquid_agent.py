"""Equity Flow Agent — relative volume, spread quality, and price momentum proxy.

Replaces the original crypto-focused LiquidAgent (api.liquid.co always 404 for
equities). This agent uses OHLCV bars already in the AnalysisContext — no extra
API calls needed.

Signal logic
------------
relative_volume   — today's cumulative volume vs 20-day avg daily volume.
                    High rel-vol during an uptrend = institutional conviction.
spread_proxy      — (high - low) / close normalised. Tight range in direction of
                    trend = healthy; wide range against trend = distribution.
vwap_deviation    — how far price is from session VWAP. Price above VWAP and
                    pulling away = bullish; below and falling = bearish.
momentum_accel    — slope of the last 3 bars' close. Measures whether price is
                    accelerating or stalling.

All four sub-signals are blended with equal weight -> score 1..100.
Confidence rises with relative volume (more liquid = more reliable signal).
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from core.base_agent import NEUTRAL_SCORE, BaseAgent, clamp_score
from core.enums import AgentRole
from core.models import AgentEvaluation, AnalysisContext

logger = logging.getLogger(__name__)

# Minimum bars to compute a meaningful signal
_MIN_BARS = 20


class LiquidAgent(BaseAgent):
    """Equity flow quality agent — relative volume, VWAP dev, momentum accel."""

    role = AgentRole.LIQUID

    def __init__(self, *, weight: float = 0.15) -> None:
        super().__init__(weight=weight)

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        df = ctx.bars
        if df is None or df.empty or len(df) < _MIN_BARS:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.05,
                rationale="insufficient bars for flow analysis",
            )

        signals: dict[str, float] = {}

        # ── 1. Relative volume ───────────────────────────────────────────────
        rel_vol_signal = self._relative_volume_signal(df)
        if rel_vol_signal is not None:
            signals["rel_vol"] = rel_vol_signal

        # ── 2. VWAP deviation ────────────────────────────────────────────────
        vwap_signal = self._vwap_deviation_signal(df)
        if vwap_signal is not None:
            signals["vwap_dev"] = vwap_signal

        # ── 3. Momentum acceleration ─────────────────────────────────────────
        accel_signal = self._momentum_accel_signal(df)
        if accel_signal is not None:
            signals["mom_accel"] = accel_signal

        # ── 4. Spread quality ────────────────────────────────────────────────
        spread_signal = self._spread_signal(df)
        if spread_signal is not None:
            signals["spread"] = spread_signal

        if not signals:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.05,
                rationale="all sub-signals failed",
            )

        score = clamp_score(float(np.mean(list(signals.values()))))
        spread_std = float(np.std(list(signals.values()))) if len(signals) > 1 else 0.0
        confidence = max(0.25, min(0.90, 0.4 - spread_std / 80.0))   # 0.90 universal cap

        rv_raw = self._relative_volume_raw(df)
        if rv_raw is not None and rv_raw > 2.0:
            confidence = min(0.90, confidence + 0.15)   # 0.90 universal cap

        label_parts = [f"{k}={v:.0f}" for k, v in signals.items()]

        intraday_dir = self._intraday_direction(df)
        dir_str = {1: "up", -1: "down", 0: "flat"}[intraday_dir]

        def _dir(s: float) -> str:
            return "bullish" if s > 60 else ("bearish" if s < 40 else "neutral")

        _signal_meta = {
            "rel_vol":   ("Relative Volume",        f"{rv_raw:.2f}x 20-day avg" if rv_raw else "N/A", f"Today's volume is {rv_raw:.1f}x the 20-day average daily volume; price is {dir_str}" if rv_raw else "N/A"),
            "vwap_dev":  ("VWAP Deviation",          "see score",                                       "Price deviation from session VWAP — positive=above VWAP (bullish), negative=below (bearish)"),
            "mom_accel": ("Momentum Acceleration",   "last 3 bars",                                     "Slope of last 3 bars — positive=price speeding up, negative=decelerating"),
            "spread":    ("Spread Quality (H-L/C)",  "last 5 bars avg",                                 "Tight spread in trend direction=healthy flow; wide spread against trend=distribution"),
        }
        signal_details = []
        for key, sig_score in signals.items():
            display, raw_str, note = _signal_meta.get(key, (key, "N/A", ""))
            signal_details.append({
                "name": key,
                "display": display,
                "raw": raw_str,
                "score": round(sig_score, 1),
                "direction": _dir(sig_score),
                "note": note,
            })

        return AgentEvaluation(
            role=self.role,
            score=score,
            confidence=round(confidence, 2),
            rationale=" | ".join(label_parts),
            data={"signals": signals},
            reasoning={
                "signals": signal_details,
                "relative_volume": round(rv_raw, 2) if rv_raw is not None else None,
                "intraday_direction": dir_str,
                "note": "Equity flow quality: high relative volume + trend-aligned signals = institutional conviction",
            },
        )

    # ── Sub-signals ───────────────────────────────────────────────────────────

    def _relative_volume_raw(self, df: pd.DataFrame) -> Optional[float]:
        """Today's cumulative volume / 20-day avg daily volume."""
        try:
            today_vol = self._today_volume(df)
            if today_vol <= 0:
                return None
            daily = df.groupby(df.index.map(lambda x: x.date()))["volume"].sum()
            daily = daily[daily > 0]
            if len(daily) < 5:
                return None
            avg_daily_vol = float(daily.iloc[:-1].tail(20).mean())  # exclude today
            return today_vol / avg_daily_vol if avg_daily_vol > 0 else None
        except Exception:
            return None

    def _relative_volume_signal(self, df: pd.DataFrame) -> Optional[float]:
        """Map relative volume ratio to 1..100 directional score."""
        rv = self._relative_volume_raw(df)
        if rv is None:
            return None
        price_dir = self._intraday_direction(df)
        if price_dir == 0:
            return 50.0
        # rv=1 = avg vol (neutral), rv=3 = 3x avg (strong institutional interest)
        base_signal = float(np.interp(rv, [0.3, 0.7, 1.0, 1.5, 2.0, 3.0],
                                      [35, 42, 50, 58, 68, 82]))
        # Flip signal for downtrending stocks (high vol + down = distribution = bearish)
        if price_dir < 0:
            base_signal = 100.0 - base_signal
        return base_signal

    def _intraday_direction(self, df: pd.DataFrame) -> int:
        """Return +1 if price is up from today's open, -1 if down, 0 if flat."""
        try:
            today_df = self._today_bars(df)
            if today_df is None or today_df.empty:
                return 0
            open_px  = float(today_df["open"].iloc[0])
            close_px = float(today_df["close"].iloc[-1])
            chg = (close_px - open_px) / open_px
            if chg >  0.001:
                return 1
            if chg < -0.001:
                return -1
            return 0
        except Exception:
            return 0

    def _vwap_deviation_signal(self, df: pd.DataFrame) -> Optional[float]:
        """Price deviation from session VWAP -> directional score."""
        try:
            today_df = self._today_bars(df)
            if today_df is None or today_df.empty or len(today_df) < 3:
                return None
            typical = (today_df["high"] + today_df["low"] + today_df["close"]) / 3.0
            cum_vol_last = float(today_df["volume"].cumsum().iloc[-1])
            # Guard: zero cumulative volume (halted/synthetic bars) → no VWAP signal.
            # The previous .replace(0, np.nan) approach let NaN slip past the
            # `is not None` guard and corrupt the blended score.
            if cum_vol_last <= 0:
                return None
            vwap    = float((typical * today_df["volume"]).cumsum().iloc[-1] / cum_vol_last)
            last_px = float(today_df["close"].iloc[-1])
            dev_pct = (last_px - vwap) / vwap * 100   # signed %
            # dev=+2% -> ~80, dev=0 -> 50, dev=-2% -> ~20
            score = float(np.clip(50.0 + dev_pct * 15.0, 1.0, 100.0))
            return score
        except Exception:
            return None

    def _momentum_accel_signal(self, df: pd.DataFrame) -> Optional[float]:
        """Acceleration: slope of last 3 bars. Positive = speeding up."""
        try:
            closes = df["close"].tail(4).values.astype(float)
            if len(closes) < 4:
                return None
            d1 = closes[-2] - closes[-3]   # bar-over-bar change, 2 bars ago
            d2 = closes[-1] - closes[-2]   # bar-over-bar change, last bar
            scale = closes[-1]
            if scale < 0.01:
                return None
            # Combined normalised momentum over last 2 bars
            accel = (d1 / scale + d2 / scale) * 100
            score = float(np.clip(50.0 + accel * 1500.0, 1.0, 100.0))
            return score
        except Exception:
            return None

    def _spread_signal(self, df: pd.DataFrame) -> Optional[float]:
        """High-low spread quality. Tight + trending = healthy flow."""
        try:
            last_5    = df.tail(5)
            spread    = float(((last_5["high"] - last_5["low"]) / last_5["close"]).mean())
            price_dir = self._intraday_direction(df)
            if price_dir == 0:
                return 50.0
            # Moderate spread is healthy; too tight or too wide is suspect
            spread_score = float(np.interp(
                spread,
                [0.001, 0.003, 0.007, 0.015, 0.025],
                [50,    58,    65,    55,    40],
            ))
            if price_dir < 0:
                spread_score = 100.0 - spread_score
            return spread_score
        except Exception:
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _today_bars(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """Return only today's bars (tz-aware DatetimeIndex safe)."""
        try:
            if len(df.index) == 0:
                return None
            today    = df.index[-1].date()
            today_df = df[df.index.map(lambda x: x.date()) == today]
            return today_df if not today_df.empty else None
        except Exception:
            return None

    def _today_volume(self, df: pd.DataFrame) -> float:
        today_df = self._today_bars(df)
        if today_df is None or today_df.empty:
            return 0.0
        return float(today_df["volume"].sum())
