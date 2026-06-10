"""News providers for the Fundamental agent."""
from __future__ import annotations

import abc
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

import aiohttp

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class Headline:
    ticker: str
    title: str
    summary: str
    published_at: datetime
    url: str = ""
    source: str = ""


class NewsSource(abc.ABC):
    @abc.abstractmethod
    async def fetch_headlines(self, ticker: str, *, limit: int = 20) -> Sequence[Headline]:
        ...

    async def get_news(self, ticker: str, limit: int = 8) -> list[dict]:
        """Headlines as plain dicts — the interface FundamentalAgent consumes.

        Without this, FundamentalAgent's news fetch raised AttributeError and
        was silently swallowed, so the live bot never saw a single headline.
        """
        headlines = await self.fetch_headlines(ticker, limit=limit)
        return [{"headline": h.title, "summary": h.summary} for h in headlines]


class PoliStockSource(NewsSource):
    """Adapter placeholder. Fill in only with authorised API access."""

    def __init__(self, base_url: str, api_key: str = "", *, timeout_s: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        # FIX: reuse a single session across calls rather than creating one per request
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch_headlines(self, ticker: str, *, limit: int = 20) -> Sequence[Headline]:
        if not self.api_key:
            logger.warning("PoliStockSource has no API key; returning no headlines.")
            return []
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {"symbol": ticker, "limit": str(limit)}
        url = f"{self.base_url}/api/news"
        try:
            session = await self._get_session()
            async with session.get(url, headers=headers, params=params) as resp:
                resp.raise_for_status()
                payload = await resp.json()
        except aiohttp.ClientError as exc:
            logger.error("news fetch failed for %s: %s", ticker, exc)
            return []
        return [
            Headline(
                ticker=ticker,
                title=item.get("title", ""),
                summary=item.get("summary", ""),
                published_at=datetime.fromisoformat(item["published_at"]),
                url=item.get("url", ""),
                source="polistock",
            )
            for item in payload.get("items", [])
        ]


class AlpacaNewsSource(NewsSource):
    """Drop-in alternative: Alpaca's news API."""

    def __init__(self, key_id: str, secret: str) -> None:
        self._key_id = key_id
        self._secret = secret
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            from alpaca.data.historical.news import NewsClient  # type: ignore
            self._client = NewsClient(self._key_id, self._secret)
        return self._client

    async def fetch_headlines(self, ticker: str, *, limit: int = 20) -> Sequence[Headline]:
        from alpaca.data.requests import NewsRequest  # type: ignore

        client = self._ensure_client()
        req = NewsRequest(symbols=ticker, limit=limit)
        # FIX: client.get_news() is synchronous — run in a thread to avoid blocking
        news = await asyncio.to_thread(client.get_news, req)
        return [
            Headline(
                ticker=ticker,
                title=a.headline,
                summary=a.summary or "",
                published_at=a.created_at,
                url=a.url or "",
                source="alpaca",
            )
            for a in news.data.get("news", [])
        ]
