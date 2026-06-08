"""MarketIntelNewsSource — AI4Trade market intelligence as a news feed.

Fetches financial event snapshots from ai4trade.ai/api/market-intel and
converts them into Headline objects for the FundamentalAgent.

This is a drop-in replacement (or supplement) for AlpacaNewsSource — it
gives you macro events, earnings alerts, and analyst upgrades/downgrades
aggregated by the AI4Trade platform.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Sequence

from data.ai4trade_client import AI4TradeClient
from data.news_sources import Headline, NewsSource

logger = logging.getLogger(__name__)


class MarketIntelNewsSource(NewsSource):
    """News source backed by AI4Trade's market-intelligence endpoint."""

    def __init__(self, client: AI4TradeClient) -> None:
        self.client = client

    async def fetch_headlines(self, ticker: str, *, limit: int = 20) -> Sequence[Headline]:
        try:
            items = await self.client.get_market_intel(symbol=ticker)
        except Exception:
            logger.exception("MarketIntelNewsSource fetch failed for %s", ticker)
            return []

        headlines = []
        for item in items[:limit]:
            try:
                pub_raw = item.get("published_at") or item.get("created_at") or item.get("timestamp")
                if isinstance(pub_raw, (int, float)):
                    pub = datetime.fromtimestamp(pub_raw, tz=timezone.utc)
                elif pub_raw:
                    pub = datetime.fromisoformat(str(pub_raw).replace("Z", "+00:00"))
                else:
                    pub = datetime.now(tz=timezone.utc)

                headlines.append(Headline(
                    ticker=ticker,
                    title=str(item.get("title") or item.get("headline") or ""),
                    summary=str(item.get("summary") or item.get("content") or ""),
                    published_at=pub,
                    url=str(item.get("url") or ""),
                    source="ai4trade-market-intel",
                ))
            except Exception:
                continue

        logger.debug("MarketIntelNewsSource: %d headlines for %s", len(headlines), ticker)
        return headlines


class CombinedNewsSource(NewsSource):
    """Fan-out news source that merges results from multiple providers.

    Useful for combining AlpacaNewsSource with MarketIntelNewsSource so the
    FundamentalAgent sees both Alpaca headlines and AI4Trade intel.
    """

    def __init__(self, *sources: NewsSource) -> None:
        self.sources = list(sources)

    async def fetch_headlines(self, ticker: str, *, limit: int = 20) -> Sequence[Headline]:
        import asyncio
        results = await asyncio.gather(
            *[s.fetch_headlines(ticker, limit=limit) for s in self.sources],
            return_exceptions=True,
        )
        combined: list[Headline] = []
        for r in results:
            if isinstance(r, Exception):
                logger.debug("CombinedNewsSource sub-source error: %s", r)
                continue
            combined.extend(r)
        # Sort by recency, deduplicate by title
        seen: set[str] = set()
        unique = []
        for h in sorted(combined, key=lambda x: x.published_at, reverse=True):
            if h.title not in seen:
                seen.add(h.title)
                unique.append(h)
        return unique[:limit]
