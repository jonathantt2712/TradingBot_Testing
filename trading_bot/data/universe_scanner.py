"""Universe Scanner — automatically selects trading candidates from the full market.

Replaces manual ticker entry. On each scan cycle it:

  1. Fetches Alpaca's most-active stocks by volume (top 50)
  2. Fetches Alpaca's top gainers + top losers (top 20 each)
  3. Merges + deduplicates
  4. Applies hard filters: price range, minimum volume, excludes ETFs/funds
  5. Scores each candidate by momentum (|day_chg| × volume_ratio)
  6. Returns the top N symbols for the full agent pipeline

Usage:
    scanner = UniverseScanner(alpaca_key, alpaca_secret)
    tickers = await scanner.get_candidates(top_n=20)
    # → ['NVDA', 'TSLA', 'META', ...]
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# Alpaca Data API base (v2)
_DATA_BASE = "https://data.alpaca.markets/v2"

# ── Hard filters ──────────────────────────────────────────────────────────────
MIN_PRICE        = 5.0      # skip penny stocks
MAX_PRICE        = 2000.0   # skip Berkshire-class outliers
MIN_VOLUME       = 500_000  # minimum shares traded today
MIN_CHANGE_PCT   = 0.5      # must be moving at least 0.5% from open

# Rough list of ETF/fund name keywords — we skip these
_ETF_KEYWORDS = re.compile(
    r"\b(etf|fund|trust|index|ishares|spdr|invesco|vanguard|proshares|direxion"
    r"|ultra|2x|3x|leveraged|inverse|bond|treasury|commodity)\b",
    re.IGNORECASE,
)

# Static set of well-known ETF tickers to always exclude
_ETF_TICKERS = {
    "SPY","QQQ","IWM","DIA","GLD","SLV","TLT","HYG","LQD","VXX","VIXY",
    "UVXY","SQQQ","TQQQ","SPXU","SPXS","SPXL","UPRO","SOXL","SOXS",
    "XLF","XLE","XLK","XLV","XLI","XLP","XLU","XLB","XLY","XLRE",
    "EEM","EFA","VTI","VOO","IVV","AGG","BND","BNDX","IEFA","IEMG",
    "ARKK","ARKG","ARKW","ARKF","ARKQ","ARKX",
}


class UniverseScanner:
    """Pull live market movers from Alpaca and filter to tradeable candidates."""

    def __init__(self, alpaca_key: str, alpaca_secret: str) -> None:
        self._key    = alpaca_key
        self._secret = alpaca_secret
        self._headers = {
            "APCA-API-KEY-ID":     alpaca_key,
            "APCA-API-SECRET-KEY": alpaca_secret,
        }

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_candidates(
        self,
        top_n:      int   = 20,
        min_price:  float = MIN_PRICE,
        max_price:  float = MAX_PRICE,
        min_volume: int   = MIN_VOLUME,
        min_change: float = MIN_CHANGE_PCT,
    ) -> list[str]:
        """Return up to top_n ticker symbols that pass all filters.

        Combines most-active + gainers + losers, deduplicates, scores by
        momentum, and returns the best candidates.
        """
        async with aiohttp.ClientSession(headers=self._headers) as session:
            active_task  = self._fetch_most_active(session, top=100)
            movers_task  = self._fetch_market_movers(session, top=50)

            active_raw, movers_raw = await asyncio.gather(
                active_task, movers_task, return_exceptions=True
            )

        candidates: dict[str, dict] = {}

        # Most active by volume
        if isinstance(active_raw, list):
            for item in active_raw:
                sym = item.get("symbol", "").upper()
                if sym:
                    candidates[sym] = item

        # Gainers + losers
        if isinstance(movers_raw, dict):
            for item in movers_raw.get("gainers", []) + movers_raw.get("losers", []):
                sym = item.get("symbol", "").upper()
                if sym and sym not in candidates:
                    candidates[sym] = item

        logger.info("Universe raw candidates: %d symbols", len(candidates))

        # Apply filters
        filtered = []
        for sym, data in candidates.items():
            reason = self._filter(sym, data, min_price, max_price, min_volume, min_change)
            if reason:
                logger.debug("  SKIP %s: %s", sym, reason)
            else:
                score = self._momentum_score(data)
                filtered.append((sym, score))

        # Sort by momentum score descending, take top N
        filtered.sort(key=lambda x: x[1], reverse=True)
        result = [sym for sym, _ in filtered[:top_n]]

        logger.info(
            "Universe filtered to %d candidates (top %d): %s",
            len(filtered), top_n, result
        )
        return result

    async def get_breakouts(
        self,
        existing_tickers: "set[str]",
        min_change_pct: float = 3.0,
        min_volume:     int   = MIN_VOLUME,
        min_price:      float = MIN_PRICE,
        top:            int   = 10,
    ) -> "list[str]":
        """Return tickers with a sudden large move not already in the watchlist.

        Runs every 5 minutes between full scans to catch news-driven eruptions
        that the 30-minute rescan would otherwise miss.
        """
        async with aiohttp.ClientSession(headers=self._headers) as session:
            movers_raw = await self._fetch_market_movers(session, top=25)

        if not isinstance(movers_raw, dict):
            return []

        candidates: list[tuple[str, float]] = []
        for item in movers_raw.get("gainers", []) + movers_raw.get("losers", []):
            sym = item.get("symbol", "").upper()
            if not sym or sym in existing_tickers or sym in _ETF_TICKERS:
                continue
            if "/" in sym or "." in sym:
                continue
            name = item.get("name", "") or item.get("company_name", "") or ""
            if _ETF_KEYWORDS.search(name):
                continue

            chg   = abs(float(item.get("percent_change") or item.get("change_percent") or 0))
            price = float(item.get("price") or item.get("close") or item.get("last_price") or 0)
            vol   = int(item.get("volume") or item.get("trade_volume") or 0)

            if chg < min_change_pct:
                continue
            if price > 0 and (price < min_price or price > MAX_PRICE):
                continue
            if vol > 0 and vol < min_volume:
                continue

            candidates.append((sym, self._momentum_score(item)))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return [sym for sym, _ in candidates[:top]]

    # ── Alpaca endpoints ──────────────────────────────────────────────────────

    async def _fetch_most_active(
        self, session: aiohttp.ClientSession, top: int = 50
    ) -> list:
        """GET /v2/stocks/most_actives — top stocks by volume today."""
        url = f"{_DATA_BASE}/stocks/most_actives"
        params = {"by": "volume", "top": top}
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning("most_actives HTTP %d: %s", resp.status, text[:200])
                    return []
                data = await resp.json()
                # Response: {"most_actives": [{symbol, volume, trade_count, ...}]}
                return data.get("most_actives", [])
        except Exception as exc:
            logger.warning("most_actives fetch failed: %s", exc)
            return []

    async def _fetch_market_movers(
        self, session: aiohttp.ClientSession, top: int = 25
    ) -> dict:
        """GET /v2/stocks/market_movers — top gainers and losers by % change."""
        url = f"{_DATA_BASE}/stocks/market_movers"
        params = {"by": "percent_change", "top": top}
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning("market_movers HTTP %d: %s", resp.status, text[:200])
                    return {}
                data = await resp.json()
                # Response: {"gainers": [...], "losers": [...]}
                return data
        except Exception as exc:
            logger.warning("market_movers fetch failed: %s", exc)
            return {}

    # ── Filter logic ──────────────────────────────────────────────────────────

    def _filter(
        self,
        sym:        str,
        data:       dict,
        min_price:  float,
        max_price:  float,
        min_volume: int,
        min_change: float,
    ) -> Optional[str]:
        """Return a reason string if the ticker should be skipped, else None."""

        # Skip known ETFs
        if sym in _ETF_TICKERS:
            return "known ETF"

        # Skip symbols with slashes (share classes like BRK/B)
        if "/" in sym or "." in sym:
            return "non-standard symbol"

        # Skip if name looks like an ETF/fund
        name = data.get("name", "") or data.get("company_name", "") or ""
        if _ETF_KEYWORDS.search(name):
            return f"ETF/fund name: {name}"

        # Price filter — Alpaca movers include close price
        price = (
            data.get("price")
            or data.get("close")
            or data.get("last_price")
            or 0.0
        )
        try:
            price = float(price)
        except (TypeError, ValueError):
            price = 0.0

        if price > 0:
            if price < min_price:
                return f"price ${price:.2f} < ${min_price}"
            if price > max_price:
                return f"price ${price:.2f} > ${max_price}"

        # Volume filter
        volume = data.get("volume") or data.get("trade_volume") or 0
        try:
            volume = int(volume)
        except (TypeError, ValueError):
            volume = 0

        if volume > 0 and volume < min_volume:
            return f"volume {volume:,} < {min_volume:,}"

        # Proxy market-cap filter: price × daily volume > $50M (avoids micro-caps with wide spreads)
        market_cap_proxy = price * volume
        if market_cap_proxy > 0 and market_cap_proxy < 50_000_000:
            return f"market-cap proxy ${market_cap_proxy/1e6:.1f}M < $50M"

        # Must be moving
        chg = abs(data.get("percent_change") or data.get("change_percent") or 0.0)
        try:
            chg = abs(float(chg))
        except (TypeError, ValueError):
            chg = 0.0

        # Only apply change filter if we have the data (movers always have it)
        if chg > 0 and chg < min_change:
            return f"|change| {chg:.2f}% < {min_change}%"

        return None  # passes all filters

    def _momentum_score(self, data: dict) -> float:
        """Score = |day_change%| × log(volume_ratio).

        Higher means more liquid AND more directional = better candidate.
        """
        import math
        chg = abs(data.get("percent_change") or data.get("change_percent") or 0.0)
        try:
            chg = abs(float(chg))
        except (TypeError, ValueError):
            chg = 0.0

        volume = data.get("volume") or data.get("trade_volume") or 1
        try:
            volume = max(int(volume), 1)
        except (TypeError, ValueError):
            volume = 1

        # Normalize volume relative to MIN_VOLUME baseline
        vol_factor = math.log(max(volume / MIN_VOLUME, 1.0) + 1)
        return chg * vol_factor
