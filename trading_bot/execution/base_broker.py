"""Abstract broker interface — all concrete brokers implement this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from core.models import TradeDecision


@dataclass
class OrderReceipt:
    """Returned by submit_bracket() after an order is placed."""
    order_id:  str
    status:    str
    ticker:    str
    side:      str
    qty:       float
    filled_at: Optional[float] = None
    metadata:  dict            = field(default_factory=dict)


class BaseBroker(ABC):
    """Common interface for Alpaca, IBKR, and mock brokers."""

    # ── Market data ───────────────────────────────────────────────────────────

    @abstractmethod
    async def get_bars(
        self,
        symbol:    str,
        timeframe: str  = "5Min",
        limit:     int  = 200,
    ) -> pd.DataFrame:
        """Return OHLCV DataFrame with a DatetimeIndex."""

    @abstractmethod
    async def get_account(self) -> dict:
        """Return account info dict with at least 'equity' and 'buying_power'.

        MUST return an empty dict (or one without a positive 'equity') when the
        account state cannot be fetched. Never fabricate equity — callers treat
        a missing equity as "do not trade".
        """

    # ── Portfolio state ───────────────────────────────────────────────────────

    async def get_positions(self) -> list[dict]:
        """Return open positions as dicts with at least 'symbol' and 'qty'.

        Brokers that cannot report positions return []; the PortfolioManager
        then has no duplicate-position protection for that broker.
        """
        return []

    async def get_open_orders(self) -> list[dict]:
        """Return open orders as dicts with at least 'symbol'."""
        return []

    async def close_all_positions(self) -> bool:
        """Flatten the book: cancel open orders and close every position.

        Returns True if the request was issued successfully.
        """
        return False

    async def get_order(self, order_id: str) -> Optional[dict]:
        """Return order state ('status', 'filled_avg_price', 'filled_qty').

        Used for post-trade fill/slippage tracking. Brokers that cannot
        report order state return None.
        """
        return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a single open order by ID. Returns True on success.

        Brokers that do not support individual order cancellation return False.
        """
        return False

    async def submit_stop(
        self,
        symbol: str,
        qty: int,
        side: str,
        stop_price: float,
    ) -> Optional[str]:
        """Submit a standalone stop order. Returns the order_id or None on failure.

        Used by the breakeven-lock loop to replace the bracket stop leg.
        Brokers that do not support standalone stop orders return None.
        """
        return None

    # ── Order management ──────────────────────────────────────────────────────

    @abstractmethod
    async def submit_bracket(self, decision: TradeDecision) -> Optional[OrderReceipt]:
        """Place a bracket order (entry + stop + take-profit)."""

    # ── Context manager support ───────────────────────────────────────────────

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass
