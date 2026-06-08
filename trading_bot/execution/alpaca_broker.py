"""Alpaca broker — REST API v2 for paper and live trading."""
from __future__ import annotations

import logging
import uuid
from typing import Optional

import pandas as pd

from core.enums import Decision
from core.models import TradeDecision
from execution.base_broker import BaseBroker, OrderReceipt

logger = logging.getLogger(__name__)

_PAPER_BASE  = "https://paper-api.alpaca.markets"
_LIVE_BASE   = "https://api.alpaca.markets"
_DATA_BASE   = "https://data.alpaca.markets"

# Map our timeframe strings to Alpaca's format
_TF_MAP = {
    "1Min": "1Min", "5Min": "5Min", "15Min": "15Min",
    "1Hour": "1Hour", "1Day": "1Day",
}


class AlpacaBroker(BaseBroker):
    """Thin async wrapper around Alpaca REST v2."""

    def __init__(self, key_id: str, secret: str, *, paper: bool = True) -> None:
        self._key    = key_id
        self._secret = secret
        self._paper  = paper
        self._base   = _PAPER_BASE if paper else _LIVE_BASE
        self._headers = {
            "APCA-API-KEY-ID":     key_id,
            "APCA-API-SECRET-KEY": secret,
        }
        self._session = None   # aiohttp.ClientSession, opened in __aenter__

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self):
        import aiohttp
        self._session = aiohttp.ClientSession(headers=self._headers)
        return self

    async def __aexit__(self, *_):
        if self._session:
            await self._session.close()
            self._session = None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _session_or_new(self):
        """Return the open session, or create a temporary one."""
        import aiohttp
        if self._session and not self._session.closed:
            return self._session, False
        return aiohttp.ClientSession(headers=self._headers), True

    async def _get(self, url: str, params: dict | None = None) -> dict | list:
        import aiohttp
        session, owned = self._session_or_new()
        try:
            async with session.get(url, params=params,
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                return await resp.json()
        finally:
            if owned:
                await session.close()

    async def _post(self, url: str, body: dict) -> dict:
        import aiohttp
        session, owned = self._session_or_new()
        try:
            async with session.post(url, json=body,
                                    timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                return await resp.json()
        finally:
            if owned:
                await session.close()

    # ── Market data ───────────────────────────────────────────────────────────

    async def get_bars(
        self,
        symbol:    str,
        timeframe: str = "5Min",
        limit:     int = 200,
    ) -> pd.DataFrame:
        tf = _TF_MAP.get(timeframe, timeframe)
        url = f"{_DATA_BASE}/v2/stocks/{symbol}/bars"
        params = {"timeframe": tf, "limit": limit, "feed": "iex", "adjustment": "raw"}
        try:
            data = await self._get(url, params)
            bars_raw = data.get("bars", [])
            if not bars_raw:
                return pd.DataFrame()
            df = pd.DataFrame(bars_raw)
            df = df.rename(columns={
                "t": "timestamp", "o": "open", "h": "high",
                "l": "low",       "c": "close", "v": "volume",
            })
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()
            return df[["open", "high", "low", "close", "volume"]]
        except Exception as exc:
            logger.warning("get_bars(%s) failed: %s", symbol, exc)
            return pd.DataFrame()

    async def get_account(self) -> dict:
        try:
            data = await self._get(f"{self._base}/v2/account")
            return {
                "equity":       float(data.get("equity", 0)),
                "buying_power": float(data.get("buying_power", 0)),
                "cash":         float(data.get("cash", 0)),
                "portfolio_value": float(data.get("portfolio_value", 0)),
            }
        except Exception as exc:
            logger.warning("get_account failed: %s", exc)
            return {"equity": 100_000.0, "buying_power": 100_000.0, "cash": 100_000.0}

    # ── Order management ──────────────────────────────────────────────────────

    async def submit_bracket(self, decision: TradeDecision) -> Optional[OrderReceipt]:
        if not decision.is_actionable or not decision.risk:
            return None

        plan   = decision.risk
        side   = "buy" if decision.decision is Decision.LONG else "sell"
        sl     = plan.stop_loss
        tp     = plan.take_profit
        qty    = int(plan.qty)

        if qty <= 0:
            logger.warning("%s: qty=%d — skipping order", decision.ticker, qty)
            return None

        body = {
            "symbol":        decision.ticker,
            "qty":           str(qty),
            "side":          side,
            "type":          "market",
            "time_in_force": "day",
            "order_class":   "bracket",
            "stop_loss":     {"stop_price": str(round(sl, 2))},
            "take_profit":   {"limit_price": str(round(tp, 2))},
        }

        try:
            resp = await self._post(f"{self._base}/v2/orders", body)
            return OrderReceipt(
                order_id  = resp.get("id", str(uuid.uuid4())),
                status    = resp.get("status", "submitted"),
                ticker    = decision.ticker,
                side      = side,
                qty       = qty,
                metadata  = resp,
            )
        except Exception as exc:
            logger.error("submit_bracket(%s) failed: %s", decision.ticker, exc)
            return None
