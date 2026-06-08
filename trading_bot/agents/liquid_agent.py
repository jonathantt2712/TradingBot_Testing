"""Liquid Positioning Agent — crowd sentiment & funding-rate signal.

Uses Liquid's real-time market data (open interest, funding rate, long/short
ratio, whale positioning) to produce a 1..100 directional score.

Signal logic:
  * Funding rate: negative funding → longs paying shorts → bearish overcrowding
    → fade the crowd (bearish signal); positive funding → bullish overcrowding
    → fade the crowd (bearish signal for longs, bullish for potential reversal).
    We use a contrarian interpretation common in crypto perps.
  * Long/short ratio: extreme long skew (>70%) → crowded → caution.
    Balanced (45-55%) → neutral. Extreme short skew → potential squeeze.
  * Open interest trend: rising OI with price up = conviction; rising OI with
    price down = distribution (bearish).

The agent uses the Liquid REST API. If no API key or network access is
available it degrades gracefully to neutral.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp
import numpy as np

from core.base_agent import NEUTRAL_SCORE, BaseAgent, clamp_score
from core.enums import AgentRole
from core.models import AgentEvaluation, AnalysisContext

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.liquid.co"
_TIMEOUT = aiohttp.ClientTimeout(total=10.0)


class LiquidAgent(BaseAgent):
    """4th directional agent using Liquid positioning & funding data."""

    role = AgentRole.LIQUID  # we'll add this to AgentRole enum

    def __init__(self, *, weight: float = 0.20, api_key: str = "") -> None:
        super().__init__(weight=weight)
        self.api_key = api_key

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        data = await self._fetch_market_data(ctx.ticker)
        if data is None:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.1,
                rationale="Liquid market data unavailable",
            )

        signals: dict[str, float] = {}

        # --- Funding rate signal (contrarian) ---------------------------
        funding_rate = data.get("funding_rate", 0.0)
        # High positive funding = longs paying too much = bearish (crowd overextended)
        # High negative funding = shorts paying too much = bullish (squeeze potential)
        funding_signal = float(np.clip(50 - funding_rate * 5000, 1, 100))
        signals["funding"] = funding_signal

        # --- Long/short ratio (contrarian at extremes) ------------------
        long_pct = data.get("long_ratio", 0.5) * 100  # 0..1 -> 0..100
        # Balanced ~50% is neutral; >70% longs = crowded = bearish fade
        # <30% longs = extreme short = squeeze bullish
        if long_pct > 55:
            ls_signal = float(np.interp(long_pct, [55, 70, 85], [50, 30, 10]))
        elif long_pct < 45:
            ls_signal = float(np.interp(long_pct, [15, 30, 45], [90, 70, 50]))
        else:
            ls_signal = 50.0
        signals["long_short"] = ls_signal

        # --- OI trend signal --------------------------------------------
        oi_change_pct = data.get("oi_change_24h_pct", 0.0)
        price_change_pct = data.get("price_change_24h_pct", 0.0)
        if oi_change_pct > 0 and price_change_pct > 0:
            oi_signal = float(np.clip(60 + oi_change_pct * 2, 50, 90))  # rising OI + rising price = bullish
        elif oi_change_pct > 0 and price_change_pct < 0:
            oi_signal = float(np.clip(40 - oi_change_pct * 2, 10, 45))  # rising OI + falling price = bearish
        elif oi_change_pct < 0:
            oi_signal = 50.0  # declining OI = deleveraging, neutral
        else:
            oi_signal = 50.0
        signals["oi_trend"] = oi_signal

        # --- Composite --------------------------------------------------
        score = clamp_score(float(np.mean(list(signals.values()))))
        spread = float(np.std(list(signals.values())))
        confidence = max(0.3, 1.0 - spread / 40.0)

        return AgentEvaluation(
            role=self.role,
            score=score,
            confidence=confidence,
            rationale=(
                f"funding={funding_rate:.4%} ls_ratio={long_pct:.0f}%L "
                f"OI_chg={oi_change_pct:+.1f}%"
            ),
            data={"signals": signals, **data},
        )

    async def _fetch_market_data(self, symbol: str) -> Optional[dict]:
        """Fetch Liquid positioning & funding data for a symbol."""
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.get(
                    f"{_BASE_URL}/api/v1/markets/{symbol}/positioning",
                    headers=headers,
                ) as resp:
                    if resp.status == 404:
                        logger.debug("symbol %s not found on Liquid", symbol)
                        return None
                    resp.raise_for_status()
                    return await resp.json()
        except asyncio.TimeoutError:
            logger.warning("Liquid data timed out for %s", symbol)
        except Exception:
            logger.debug("Liquid data unavailable for %s (non-fatal)", symbol)
        return None
