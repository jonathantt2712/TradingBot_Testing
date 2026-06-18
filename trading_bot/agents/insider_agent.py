"""Congressional Trading Intelligence Agent — House Stock Watcher integration.

Reads congressional trading disclosures from the free House Stock Watcher S3 JSON
(no API key required, updates daily) and scores each ticker by political insider
conviction. Requires technical confirmation before generating a directional signal
to avoid blindly following politicians.

Scoring logic:
  - More unique politicians buying = higher confidence
  - Large disclosed trade amounts ($100K+) add conviction
  - Must have ≥2 of 4 technical confirmations to avoid false signals
  - Confidence capped at 0.70 (insider signals are supplementary, not primary)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from core.base_agent import NEUTRAL_SCORE, BaseAgent, clamp_score
from core.enums import AgentRole
from core.models import AgentEvaluation, AnalysisContext

logger = logging.getLogger(__name__)

# Free public S3 endpoint — no auth needed, updated daily after market close
_HOUSE_API_URL = "https://house-stock-watcher-data.s3-us-east-2.amazonaws.com/data/all_transactions.json"
_LOOKBACK_DAYS = 30
_CACHE_TTL_SECONDS = 21600  # 6-hour cache (data updates once per day)

_LARGE_AMOUNTS = {"$100,001 - $250,000", "$250,001 - $500,000", "$500,001 - $1,000,000",
                  "$1,000,001 - $5,000,000", "over $5,000,000"}
_BUY_TYPES  = {"purchase"}
_SELL_TYPES = {"sale_full", "sale_partial", "sale"}


class InsiderAgent(BaseAgent):
    """7th directional agent using congressional trading disclosure data."""

    role = AgentRole.INSIDER

    def __init__(self, *, weight: float = 0.10) -> None:
        super().__init__(weight=weight)
        self._cache: Optional[list] = None
        self._cache_ts: float = 0.0

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        if ctx.backtest_mode:
            # Congressional disclosures are fetched as CURRENT state; replaying them
            # onto historical windows would leak future information (look-ahead bias).
            return AgentEvaluation(
                role=self.role, score=NEUTRAL_SCORE, confidence=0.0,
                rationale="insider: neutral in backtest (point-in-time data, no look-ahead)",
            )
        transactions = await self._get_transactions(ctx.ticker)
        if not transactions:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.05,
                rationale=f"no congressional trades in past {_LOOKBACK_DAYS}d",
            )

        buys  = [t for t in transactions if t.get("type", "").lower() in _BUY_TYPES]
        sells = [t for t in transactions if t.get("type", "").lower() in _SELL_TYPES]

        unique_buyers  = len(set(t.get("representative", "") for t in buys  if t.get("representative")))
        unique_sellers = len(set(t.get("representative", "") for t in sells if t.get("representative")))

        if unique_buyers == 0 and unique_sellers == 0:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.05,
                rationale=f"no valid congress trades for {ctx.ticker} in {_LOOKBACK_DAYS}d",
            )

        if unique_buyers > unique_sellers:
            # Net buying signal
            base = 62
            if unique_buyers >= 3:
                base += 12
            elif unique_buyers == 2:
                base += 7
            # Large trade bonus
            large_count = sum(1 for t in buys if t.get("amount", "") in _LARGE_AMOUNTS)
            base = min(88, base + large_count * 4)
            # Technical confirmation gate
            tech_confirms = self._count_tech_confirms(ctx.bars, bullish=True)
            if tech_confirms < 2:
                base = min(base, 57)  # cap weak signal when technicals disagree
            confidence = min(0.70, 0.25 + unique_buyers * 0.10)
            direction_str = f"{unique_buyers} congress buyers"
        elif unique_sellers > unique_buyers:
            # Net selling signal
            base = 38
            if unique_sellers >= 3:
                base -= 10
            elif unique_sellers == 2:
                base -= 5
            tech_confirms = self._count_tech_confirms(ctx.bars, bullish=False)
            if tech_confirms < 2:
                base = max(base, 43)
            confidence = min(0.60, 0.20 + unique_sellers * 0.08)
            direction_str = f"{unique_sellers} congress sellers"
        else:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.08,
                rationale=f"mixed: {unique_buyers} buyers, {unique_sellers} sellers",
            )

        # Cap confidence to 0.90 (universal cap)
        confidence = min(0.90, confidence)

        return AgentEvaluation(
            role=self.role,
            score=clamp_score(base),
            confidence=round(confidence, 3),
            rationale=f"{direction_str} past {_LOOKBACK_DAYS}d ({len(transactions)} disclosures)",
            reasoning={
                "unique_buyers":  unique_buyers,
                "unique_sellers": unique_sellers,
                "total_disclosures": len(transactions),
                "large_trades": sum(1 for t in buys if t.get("amount", "") in _LARGE_AMOUNTS),
                "score": round(clamp_score(base), 1),
                "note": (
                    "Congressional trading disclosure data from House Stock Watcher. "
                    "Signals require ≥2 technical confirmations to avoid false positives."
                ),
            },
        )

    def _count_tech_confirms(self, bars: Optional[pd.DataFrame], *, bullish: bool) -> int:
        """Count how many of 4 technical conditions align with the trade direction."""
        if bars is None or len(bars) < 22:
            return 0
        confirms = 0
        try:
            close = bars["close"]
            ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
            last  = float(close.iloc[-1])

            # 1. Price above/below EMA-21
            if bullish and last > ema21:
                confirms += 1
            elif not bullish and last < ema21:
                confirms += 1

            # 2. RSI not overbought/oversold
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean().iloc[-1]
            loss  = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
            rsi   = (100 - 100 / (1 + gain / loss)) if loss > 0 else (100.0 if gain > 0 else 50.0)
            if bullish and rsi < 70:
                confirms += 1
            elif not bullish and rsi > 30:
                confirms += 1

            # 3. Price above/below session VWAP
            if hasattr(bars.index, "date"):
                today = bars.index[-1].date()
                today_df = bars[bars.index.map(lambda x: x.date()) == today]
                if not today_df.empty:
                    typical = (today_df["high"] + today_df["low"] + today_df["close"]) / 3.0
                    cum_vol = today_df["volume"].cumsum().replace(0, float("nan"))
                    vwap = float((typical * today_df["volume"]).cumsum().iloc[-1] / cum_vol.iloc[-1])
                    if bullish and last > vwap:
                        confirms += 1
                    elif not bullish and last < vwap:
                        confirms += 1

            # 4. MACD histogram direction
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            signal = (ema12 - ema26).ewm(span=9, adjust=False).mean()
            macd_hist = float(((ema12 - ema26) - signal).iloc[-1])
            if bullish and macd_hist > 0:
                confirms += 1
            elif not bullish and macd_hist < 0:
                confirms += 1

        except Exception:
            pass
        return confirms

    async def _get_transactions(self, ticker: str) -> list:
        """Fetch and filter congressional transactions for the given ticker."""
        all_data = await self._fetch_all_transactions()
        if not all_data:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)
        result = []
        ticker_upper = ticker.upper()

        for t in all_data:
            # Filter by ticker
            sym = (t.get("ticker") or "").strip().upper()
            if sym != ticker_upper:
                continue
            # Filter by date
            disc_date_str = t.get("disclosure_date") or t.get("transaction_date") or ""
            try:
                disc_date = datetime.fromisoformat(disc_date_str.replace("Z", "+00:00"))
                if disc_date.tzinfo is None:
                    disc_date = disc_date.replace(tzinfo=timezone.utc)
                if disc_date < cutoff:
                    continue
            except Exception:
                continue
            result.append(t)

        return result

    async def _fetch_all_transactions(self) -> Optional[list]:
        """Fetch the full House Stock Watcher dataset with 6h caching."""
        import time
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_ts) < _CACHE_TTL_SECONDS:
            return self._cache

        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    _HOUSE_API_URL,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("InsiderAgent: House Stock Watcher returned %d", resp.status)
                        return None
                    data = await resp.json(content_type=None)
            if isinstance(data, list):
                self._cache    = data
                self._cache_ts = now
                logger.info("InsiderAgent: loaded %d congressional transactions", len(data))
                return data
        except Exception as exc:
            logger.warning("InsiderAgent: fetch failed: %s", exc)
        return None
