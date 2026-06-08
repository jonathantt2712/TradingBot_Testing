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
        """Return account info dict with at least 'equity' and 'buying_power'."""

    # ── Order management ──────────────────────────────────────────────────────

    @abstractmethod
    async def submit_bracket(self, decision: TradeDecision) -> Optional[OrderReceipt]:
        """Place a bracket order (entry + stop + take-profit)."""

    # ── Context manager support ───────────────────────────────────────────────

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass
