"""IBKR broker via ib_insync — connects to TWS or IB Gateway paper trading.

TWS paper port  : 7497  (default, set in TWS Global Config → API → Settings)
IB Gateway paper: 4002
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import pandas as pd

from core.enums import Decision
from core.models import TradeDecision
from execution.base_broker import BaseBroker, OrderReceipt

logger = logging.getLogger(__name__)

# Map our timeframe strings to TWS bar sizes and history durations
_BAR_SIZE_MAP: dict[str, str] = {
    "1Min":  "1 min",
    "5Min":  "5 mins",
    "15Min": "15 mins",
    "1Hour": "1 hour",
    "1Day":  "1 day",
}

_DURATION_MAP: dict[str, str] = {
    "1Min":  "1 D",
    "5Min":  "2 D",
    "15Min": "5 D",
    "1Hour": "10 D",
    "1Day":  "200 D",
}


class IBKRBroker(BaseBroker):
    """Interactive Brokers broker via ib_insync / TWS API.

    Requires TWS or IB Gateway running locally with:
      • "Enable ActiveX and Socket Clients" checked
      • Socket port = 7497 (TWS paper) or 4002 (IB Gateway paper)
      • "Read-Only API" unchecked
      • "Allow connections from localhost only" checked
    """

    def __init__(
        self,
        host:      str = "127.0.0.1",
        port:      int = 7497,
        client_id: int = 1,
    ) -> None:
        self.host           = host
        self.port           = port
        self.client_id      = client_id
        self._ib            = None
        self._reconnect_task: Optional[asyncio.Task] = None

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "IBKRBroker":
        from ib_insync import IB, util
        util.patchAsyncio()          # let ib_insync share the running event loop
        self._ib = IB()
        await self._connect()
        self._ib.disconnectedEvent += self._on_disconnect
        return self

    async def _connect(self) -> None:
        """Establish connection to TWS/IB Gateway with retry on first attempt."""
        for attempt in range(1, 4):
            try:
                await self._ib.connectAsync(
                    self.host, self.port,
                    clientId=self.client_id,
                    timeout=15,
                )
                logger.info(
                    "IBKRBroker connected to %s:%d (clientId=%d)",
                    self.host, self.port, self.client_id,
                )
                return
            except Exception as exc:
                logger.error("IBKRBroker connect attempt %d failed: %s", attempt, exc)
                if attempt < 3:
                    await asyncio.sleep(5 * attempt)
        raise ConnectionError(
            f"IBKRBroker could not connect to {self.host}:{self.port} after 3 attempts"
        )

    def _on_disconnect(self) -> None:
        """Called by ib_insync when the connection drops unexpectedly."""
        logger.warning("IBKRBroker: TWS disconnected — scheduling reconnect")
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Exponential-backoff reconnect loop: waits 5s, 15s, 30s, 60s…"""
        delays = [5, 15, 30, 60, 120]
        for i, delay in enumerate(delays):
            logger.info("IBKRBroker reconnect attempt %d in %ds…", i + 1, delay)
            await asyncio.sleep(delay)
            try:
                if not self._ib.isConnected():
                    await self._ib.connectAsync(
                        self.host, self.port,
                        clientId=self.client_id,
                        timeout=15,
                    )
                    logger.info("IBKRBroker reconnected successfully")
                    return
            except Exception as exc:
                logger.warning("IBKRBroker reconnect attempt %d failed: %s", i + 1, exc)
        logger.error("IBKRBroker: all reconnect attempts exhausted — giving up")

    async def __aexit__(self, *_) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if self._ib:
            try:
                self._ib.disconnectedEvent -= self._on_disconnect
            except Exception:
                pass
            if self._ib.isConnected():
                self._ib.disconnect()
                logger.info("IBKRBroker disconnected")
        self._ib = None

    def _require(self) -> None:
        if self._ib is None or not self._ib.isConnected():
            raise RuntimeError(
                "IBKRBroker not connected — use 'async with broker:' context"
            )

    # ── Market data ───────────────────────────────────────────────────────────

    async def get_bars(
        self,
        symbol:    str,
        timeframe: str = "5Min",
        limit:     int = 200,
    ) -> pd.DataFrame:
        self._require()
        from ib_insync import Stock
        try:
            contract  = Stock(symbol, "SMART", "USD")
            bar_size  = _BAR_SIZE_MAP.get(timeframe, "5 mins")
            duration  = _DURATION_MAP.get(timeframe, "2 D")

            bars = await self._ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",          # "" = now
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
                keepUpToDate=False,
            )

            if not bars:
                logger.warning("get_bars(%s): no data returned", symbol)
                return pd.DataFrame()

            df = pd.DataFrame([
                {
                    "timestamp": b.date,
                    "open":      b.open,
                    "high":      b.high,
                    "low":       b.low,
                    "close":     b.close,
                    "volume":    b.volume,
                }
                for b in bars
            ])
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()
            return df[["open", "high", "low", "close", "volume"]].tail(limit)

        except Exception as exc:
            logger.warning("get_bars(%s) failed: %s", symbol, exc)
            return pd.DataFrame()

    # ── Account info ──────────────────────────────────────────────────────────

    async def get_account(self) -> dict:
        self._require()
        try:
            accounts = self._ib.managedAccounts()
            account  = accounts[0] if accounts else ""
            vals     = self._ib.accountValues(account)

            def _get(tag: str, default: float = 0.0) -> float:
                for av in vals:
                    if av.tag == tag and av.currency in ("USD", "BASE", ""):
                        try:
                            return float(av.value)
                        except ValueError:
                            pass
                return default

            equity       = _get("NetLiquidation") or _get("TotalCashBalance", 100_000.0)
            buying_power = _get("BuyingPower") or equity
            cash         = _get("TotalCashBalance") or equity

            return {
                "equity":          equity,
                "buying_power":    buying_power,
                "cash":            cash,
                "portfolio_value": equity,
            }
        except Exception as exc:
            logger.warning("get_account failed: %s", exc)
            return {
                "equity": 100_000.0, "buying_power": 100_000.0,
                "cash":   100_000.0, "portfolio_value": 100_000.0,
            }

    # ── Order execution ───────────────────────────────────────────────────────

    async def submit_bracket(
        self, decision: TradeDecision
    ) -> Optional[OrderReceipt]:
        self._require()
        if not decision.is_actionable or not decision.risk:
            return None

        from ib_insync import Stock, MarketOrder, LimitOrder, StopOrder

        plan       = decision.risk
        qty        = int(plan.qty)
        if qty <= 0:
            logger.warning("%s: qty=%d — skipping order", decision.ticker, qty)
            return None

        action     = "BUY"  if decision.decision is Decision.LONG else "SELL"
        action     = "BUY"  if decision.decision is Decision.LONG else "SELL"
        rev_action = "SELL" if action == "BUY"  else "BUY"
        sl         = round(plan.stop_loss,   2)
        tp         = round(plan.take_profit, 2)

        try:
            contract = Stock(decision.ticker, "SMART", "USD")
            self._ib.qualifyContracts(contract)

            # Allocate three consecutive order IDs
            parent_id = self._ib.client.getReqId()
            tp_id     = self._ib.client.getReqId()
            sl_id     = self._ib.client.getReqId()

            # Parent: market entry
            parent           = MarketOrder(action, qty)
            parent.orderId   = parent_id
            parent.transmit  = False          # hold until all legs are ready

            # Take profit: limit order
            take_profit            = LimitOrder(rev_action, qty, tp)
            take_profit.orderId    = tp_id
            take_profit.parentId   = parent_id
            take_profit.transmit   = False

            # Stop loss: stop order — transmit=True sends all three at once
            stop_loss            = StopOrder(rev_action, qty, sl)
            stop_loss.orderId    = sl_id
            stop_loss.parentId   = parent_id
            stop_loss.transmit   = True

            parent_trade = self._ib.placeOrder(contract, parent)
            self._ib.placeOrder(contract, take_profit)
            self._ib.placeOrder(contract, stop_loss)

            # Brief pause to let TWS acknowledge
            await asyncio.sleep(0.5)

            status = parent_trade.orderStatus.status or "Submitted"
            logger.info(
                "IBKR bracket: %s %s ×%d | entry=MKT sl=%.2f tp=%.2f | "
                "status=%s | orderId=%d",
                action, decision.ticker, qty, sl, tp, status, parent_id,
            )

            return OrderReceipt(
                order_id = str(parent_id),
                status   = status,
                ticker   = decision.ticker,
                side     = action.lower(),
                qty      = qty,
            )

        except Exception as exc:
            logger.error("submit_bracket(%s) failed: %s", decision.ticker, exc)
            return None
