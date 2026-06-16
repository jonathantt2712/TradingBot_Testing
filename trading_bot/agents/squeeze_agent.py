"""Short Squeeze Detector — FINRA consolidated short sale volume.

FINRA publishes daily short volume data (free, no auth) at:
  https://regsho.finra.org/FNQCshvol{YYYYMMDD}.txt

Format (pipe-separated):
  Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market

short_ratio = ShortVolume / TotalVolume
  > 0.50  heavy shorting — squeeze risk if price trending up
  0.30–0.50  moderate
  < 0.30  low shorting

Squeeze setup: high short_ratio + price trending up + high rel_vol = shorts being
forced to cover, magnifying upward moves.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import numpy as np

from core.base_agent import NEUTRAL_SCORE, BaseAgent, clamp_score
from core.enums import AgentRole
from core.models import AgentEvaluation, AnalysisContext

logger = logging.getLogger(__name__)

_FINRA_URL = "https://regsho.finra.org/FNQCshvol{date}.txt"
_CACHE: dict = {"date": None, "data": {}}


async def _fetch_finra() -> dict[str, float]:
    """Download and parse FINRA short sale volume. Cached for the calendar day."""
    import aiohttp

    today = date.today()
    if _CACHE["date"] == str(today) and _CACHE["data"]:
        return _CACHE["data"]

    dates_to_try = [today - timedelta(days=i) for i in range(4) if (today - timedelta(days=i)).weekday() < 5]

    async with aiohttp.ClientSession() as session:
        for d in dates_to_try:
            url = _FINRA_URL.format(date=d.strftime("%Y%m%d"))
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        continue
                    text = await r.text()
            except Exception:
                continue

            data: dict[str, float] = {}
            for line in text.splitlines():
                parts = line.strip().split("|")
                if len(parts) < 5 or parts[0] == "Date":
                    continue
                try:
                    sym       = parts[1].strip().upper()
                    short_vol = float(parts[2])
                    total_vol = float(parts[4])
                    if total_vol > 0:
                        data[sym] = round(short_vol / total_vol, 4)
                except (ValueError, IndexError):
                    continue

            if data:
                _CACHE["date"] = str(today)
                _CACHE["data"] = data
                logger.info("FINRA short volume loaded: %d symbols (date=%s)", len(data), d)
                return data

    logger.warning("FINRA short volume unavailable — SqueezeAgent neutral")
    return {}


class SqueezeAgent(BaseAgent):
    """Short squeeze setup detector using FINRA daily short volume data."""

    role = AgentRole.SQUEEZE

    def __init__(self, *, weight: float = 0.08) -> None:
        super().__init__(weight=weight)

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        finra_data = await _fetch_finra()
        short_ratio = finra_data.get(ctx.ticker.upper())

        if short_ratio is None:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.05,
                rationale="no FINRA short volume data",
            )

        df = ctx.bars
        price_dir = 0
        rel_vol   = 1.0

        if df is not None and not df.empty and len(df) >= 20:
            try:
                today_bars = df[df.index.map(lambda x: x.date()) == df.index[-1].date()]
                if not today_bars.empty:
                    open_px  = float(today_bars["open"].iloc[0])
                    close_px = float(today_bars["close"].iloc[-1])
                    chg = (close_px - open_px) / open_px
                    price_dir = 1 if chg > 0.001 else (-1 if chg < -0.001 else 0)

                today_vol = float(today_bars["volume"].sum()) if not today_bars.empty else 0
                daily_vols = df.groupby(df.index.map(lambda x: x.date()))["volume"].sum()
                daily_vols = daily_vols[daily_vols > 0]
                avg_vol = float(daily_vols.iloc[:-1].tail(20).mean()) if len(daily_vols) >= 2 else 1
                rel_vol = today_vol / avg_vol if avg_vol > 0 else 1.0
            except Exception:
                pass

        if short_ratio >= 0.50:
            if price_dir > 0:
                base  = float(np.interp(short_ratio, [0.50, 0.65, 0.80], [62, 72, 82]))
                if rel_vol > 2.0:
                    base = min(90.0, base + 8.0)
                score = base
                setup = "squeeze_long"
            elif price_dir < 0:
                base  = float(np.interp(short_ratio, [0.50, 0.65, 0.80], [38, 30, 22]))
                score = base
                setup = "short_pressure"
            else:
                score = 50.0
                setup = "neutral_high_short"
        elif short_ratio >= 0.30:
            score = 50.0 + price_dir * 4.0
            setup = "moderate_short"
        else:
            score = 50.0
            setup = "low_short"

        score      = clamp_score(score)
        confidence = min(0.70, 0.30 + short_ratio * 0.5 + (0.10 if rel_vol > 1.5 else 0.0))

        return AgentEvaluation(
            role=self.role,
            score=score,
            confidence=round(confidence, 2),
            rationale=(
                f"short_ratio={short_ratio:.2%} | {setup} | "
                f"rel_vol={rel_vol:.1f}x | price={'up' if price_dir>0 else 'down' if price_dir<0 else 'flat'}"
            ),
            data={"short_ratio": round(short_ratio, 4), "setup": setup, "rel_vol": round(rel_vol, 2)},
            reasoning={
                "short_ratio":     round(short_ratio, 4),
                "short_ratio_pct": f"{short_ratio:.1%}",
                "price_direction": "up" if price_dir > 0 else "down" if price_dir < 0 else "flat",
                "relative_volume": round(rel_vol, 2),
                "setup":           setup,
                "note": (
                    "FINRA daily short volume ratio: >50% means over half of all volume is short sales. "
                    "Combined with upward price and high rel-vol → short covering (squeeze). "
                    "Combined with downward price → shorts winning."
                ),
            },
        )
