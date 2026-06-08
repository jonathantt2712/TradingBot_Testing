"""IBKR broker stub — placeholder for live Interactive Brokers integration."""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from core.models import TradeDecision
from execution.base_broker import BaseBroker, OrderReceipt

logger = logging.getLogger(__name__)


class IBKRBroker(BaseBroker):
    """Stub for Interactive Brokers via ib_insync / TWS API.

    Not implemented in this version — use AlpacaBroker for paper/live trading.
    Raise NotImplementedError on all calls to make misconfiguration obvious.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1) -> None:
        self.host      = host
        self.port      = port
        self.client_id = client_id
        logger.warning(
            "IBKRBroker instantiated — IBKR integration not implemented. "
            "Set USE_LIQUID_BROKER=false and configure Alpaca credentials instead."
        )

    async def get_bars(self, symbol: str, timeframe: str = "5Min", limit: int = 200) -> pd.DataFrame:
        raise NotImplementedError("IBKRBroker.get_bars not implemented")

    async def get_account(self) -> dict:
        raise NotImplementedError("IBKRBroker.get_account not implemented")

    async def submit_bracket(self, decision: TradeDecision) -> Optional[OrderReceipt]:
        raise NotImplementedError("IBKRBroker.submit_bracket not implemented")
