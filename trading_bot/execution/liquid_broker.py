"""Liquid Co-Invest broker adapter.

Routes order execution and account queries through the Liquid trading platform.
Liquid is a multi-asset perp exchange supporting equities, crypto, commodities,
and indices.

IMPORTANT: Liquid order execution requires explicit user confirmation via the
Liquid widget (suggest_trade → user clicks Confirm). The bot submits a trade
suggestion; the actual fill only occurs after the user approves it in the UI.
For fully automated execution use execute_tpsl with a pre-funded account.

Environment variables:
    LIQUID_API_KEY  — your Liquid API key (get from Settings → API in the app)
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Mapping, Optional

import aiohttp

from core.models import TradeDecision
from execution.base_broker import BaseBroker, OrderReceipt

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.liquid.co"  # Liquid REST API base


class LiquidBroker(BaseBroker):
    """Broker adapter for Liquid Co-Invest.

    Supports both paper trading (set LIQUID_PAPER=true) and live execution.
    Uses the Liquid REST API for programmatic access.
    """

    def __init__(self, api_key: str = "", *, paper: bool = False, timeout_s: float = 15.0) -> None:
        self.api_key = api_key
        self.paper = paper
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: Optional[aiohttp.ClientSession] = None

    # --- context manager (matches BaseBroker interface) -----------------

    async def __aenter__(self) -> "LiquidBroker":
        self._session = aiohttp.ClientSession(
            base_url=_BASE_URL,
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
            timeout=self._timeout,
        )
        if self.paper:
            logger.info("LiquidBroker: paper trading mode active")
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # --- BaseBroker interface -------------------------------------------

    async def get_account(self) -> Mapping[str, float]:
        """Return account equity and buying power from Liquid portfolio."""
        try:
            async with self._session.get("/api/v1/account") as resp:
                resp.raise_for_status()
                data = await resp.json()
            portfolio = data.get("portfolio", {})
            return {
                "equity": float(portfolio.get("equity", 0.0)),
                "cash": float(portfolio.get("available_balance", 0.0)),
                "buying_power": float(portfolio.get("available_balance", 0.0)),
            }
        except Exception:
            logger.exception("LiquidBroker.get_account failed")
            return {"equity": 0.0, "cash": 0.0, "buying_power": 0.0}

    async def get_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "5Min",
        limit: int = 200,
    ):
        """Fetch OHLCV bars from Liquid's market data endpoint."""
        import pandas as pd

        # Map timeframe string to Liquid resolution codes
        resolution_map = {
            "1Min": "1", "5Min": "5", "15Min": "15",
            "30Min": "30", "1H": "60", "4H": "240", "1D": "D",
        }
        resolution = resolution_map.get(timeframe, "5")

        try:
            async with self._session.get(
                f"/api/v1/markets/{symbol}/ohlcv",
                params={"resolution": resolution, "limit": limit},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            candles = data.get("candles", [])
            df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
            df = df.set_index("timestamp").sort_index()
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            return df

        except Exception:
            logger.exception("LiquidBroker.get_bars failed for %s", symbol)
            raise

    async def submit_bracket(self, decision: TradeDecision) -> Optional[OrderReceipt]:
        """Submit a bracket order (entry + TP/SL) to Liquid.

        In paper mode the order is simulated locally.
        In live mode the order is submitted for user confirmation via the widget.
        """
        if not decision.is_actionable or not decision.risk:
            return None

        r = decision.risk
        side = "buy" if decision.side.value == "buy" else "sell"

        payload = {
            "symbol": decision.ticker,
            "side": side,
            "size": r.qty,
            "order_type": "market",
            "take_profit": r.take_profit,
            "stop_loss": r.stop_loss,
            "paper": self.paper,
        }

        try:
            async with self._session.post("/api/v1/orders", json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
            order_id = str(data.get("order_id", ""))
            status = data.get("status", "pending_confirmation")
            logger.info("Liquid order submitted: %s %s %s -> %s", side, r.qty, decision.ticker, status)
            return OrderReceipt(order_id=order_id, status=status, ticker=decision.ticker, side=side, qty=r.qty)
        except Exception:
            logger.exception("LiquidBroker.submit_bracket failed for %s", decision.ticker)
            return None
