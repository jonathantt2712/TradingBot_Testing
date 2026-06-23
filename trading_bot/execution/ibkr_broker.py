"""IBKR broker via ib_insync — connects to TWS or IB Gateway paper trading.

TWS paper port  : 7497  (default, set in TWS Global Config → API → Settings)
IB Gateway paper: 4002
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
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

# TWS order-type strings -> the lowercase types the rest of the bot uses.
_ORDER_TYPE_MAP: dict[str, str] = {
    "STP":     "stop",
    "STP LMT": "stop_limit",
    "LMT":     "limit",
    "MKT":     "market",
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

    async def get_bars_range(
        self,
        symbol:    str,
        start:     datetime,
        end:       datetime,
        timeframe: str = "5Min",
    ) -> pd.DataFrame:
        """Historical bars covering roughly [start, end] — for backtests.

        Unlike get_bars (a short recent window tailed to a limit), this requests
        a multi-week span in one call. IBKR caps a single intraday request at
        ~30 days, so the duration is clamped; a longer window would need chunked
        endDateTime walk-back, which is not needed for the default 30-day run.
        """
        self._require()
        from ib_insync import Stock
        try:
            contract  = Stock(symbol, "SMART", "USD")
            bar_size  = _BAR_SIZE_MAP.get(timeframe, "5 mins")
            span_days = max(1, (end - start).days)
            if span_days > 30:
                logger.warning(
                    "get_bars_range(%s): %d-day span clamped to IBKR's 30-day "
                    "single-request limit for intraday bars", symbol, span_days,
                )
                span_days = 30

            bars = await self._ib.reqHistoricalDataAsync(
                contract,
                endDateTime=end,
                durationStr=f"{span_days} D",
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
                keepUpToDate=False,
            )

            if not bars:
                logger.warning("get_bars_range(%s): no data returned", symbol)
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
            return df[["open", "high", "low", "close", "volume"]]

        except Exception as exc:
            logger.warning("get_bars_range(%s) failed: %s", symbol, exc)
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

            equity       = _get("NetLiquidation") or _get("TotalCashBalance")
            buying_power = _get("BuyingPower") or equity
            cash         = _get("TotalCashBalance") or equity

            if equity <= 0:
                logger.error("get_account: no usable equity reported — trading disabled this cycle")
                return {}

            return {
                "equity":          equity,
                "buying_power":    buying_power,
                "cash":            cash,
                "portfolio_value": equity,
            }
        except Exception as exc:
            # Fail closed: empty dict means "equity unknown" → downstream refuses to size.
            logger.error("get_account failed — trading disabled this cycle: %s", exc)
            return {}

    # ── Portfolio state ───────────────────────────────────────────────────────

    async def get_positions(self) -> list[dict]:
        self._require()
        positions = await self._ib.reqPositionsAsync()
        # Best-effort enrichment with live P&L from the account's portfolio feed,
        # so the breakeven-lock loop can derive per-share P&L the same way it does
        # for Alpaca. Missing data degrades to 0.0 (loop then safely skips).
        pnl_by: dict[str, tuple[float, float]] = {}
        try:
            for item in self._ib.portfolio():
                pnl_by[item.contract.symbol] = (
                    float(item.marketValue), float(item.unrealizedPNL),
                )
        except Exception:
            logger.debug("portfolio() enrichment unavailable", exc_info=True)
        out: list[dict] = []
        for p in positions:
            if p.position == 0:
                continue
            mv, upnl = pnl_by.get(p.contract.symbol, (0.0, 0.0))
            out.append({
                "symbol": p.contract.symbol,
                "qty":    float(p.position),
                "side":   "long" if p.position > 0 else "short",
                "market_value":  mv,
                "unrealized_pl": upnl,
            })
        return out

    async def get_open_orders(self) -> list[dict]:
        self._require()
        trades = self._ib.openTrades()
        return [
            {
                "symbol": t.contract.symbol,
                "id":     str(t.order.orderId),
                "side":   t.order.action.lower(),
                "type":   _ORDER_TYPE_MAP.get(
                    t.order.orderType, str(t.order.orderType).lower()),
            }
            for t in trades
        ]

    async def get_order(self, symbol_or_id: str) -> Optional[dict]:
        """Return entry-order state for fill/slippage tracking.

        Matches the bracket parent by orderId and maps ib_insync's order status
        to the {status, filled_avg_price, filled_qty} shape the PortfolioManager
        expects. Returns None when the order isn't found — callers then skip
        slippage tracking rather than fabricate a fill.
        """
        self._require()
        try:
            oid = int(symbol_or_id)
        except (TypeError, ValueError):
            return None
        for trade in self._ib.trades():
            if trade.order.orderId != oid:
                continue
            st = trade.orderStatus
            return {
                "status":           (st.status or "").lower(),   # "Filled" -> "filled"
                "filled_avg_price": st.avgFillPrice or None,
                "filled_qty":       st.filled or None,
            }
        return None

    async def close_all_positions(self) -> bool:
        """Cancel all open orders and flatten every position with market orders."""
        self._require()
        from ib_insync import MarketOrder, Stock
        try:
            for trade in self._ib.openTrades():
                self._ib.cancelOrder(trade.order)

            positions = await self._ib.reqPositionsAsync()
            for p in positions:
                if p.position == 0:
                    continue
                action = "SELL" if p.position > 0 else "BUY"
                contract = p.contract
                if not contract.exchange:
                    contract = Stock(contract.symbol, "SMART", "USD")
                self._ib.placeOrder(contract, MarketOrder(action, abs(int(p.position))))
                logger.info("EOD flatten: %s %s ×%d", action, p.contract.symbol, abs(int(p.position)))
            return True
        except Exception as exc:
            logger.error("close_all_positions failed: %s", exc)
            return False

    # ── Order management (breakeven lock) ─────────────────────────────────────

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a single working order by ID. Returns True on success."""
        self._require()
        try:
            oid = int(order_id)
        except (TypeError, ValueError):
            return False
        for trade in self._ib.openTrades():
            if trade.order.orderId == oid:
                self._ib.cancelOrder(trade.order)
                logger.info("IBKR cancel_order: %s", oid)
                return True
        return False

    async def submit_stop(
        self, symbol: str, qty: int, side: str, stop_price: float
    ) -> Optional[str]:
        """Submit a standalone stop order (replaces the bracket stop after a
        breakeven lock). ``side``: 'sell' for LONG positions, 'buy' for SHORT.
        Returns the new order id, or None on failure.
        """
        self._require()
        from ib_insync import Stock, StopOrder
        action = "SELL" if side.lower() == "sell" else "BUY"
        try:
            contract = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            order = StopOrder(action, int(qty), round(stop_price, 2))
            trade = self._ib.placeOrder(contract, order)
            await asyncio.sleep(0.5)
            oid = str(trade.order.orderId)
            logger.info("IBKR stop submitted %s %s ×%d @ %.2f → id=%s",
                        action, symbol, qty, stop_price, oid)
            return oid
        except Exception as exc:
            logger.error("submit_stop(%s) failed: %s", symbol, exc)
            return None

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
