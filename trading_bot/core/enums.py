"""Domain enumerations shared across the trading system."""
from __future__ import annotations

from enum import Enum


class Decision(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    PASS = "PASS"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    BRACKET = "bracket"


class AgentRole(str, Enum):
    FUNDAMENTAL = "fundamental"
    VISION = "vision"
    TECHNICAL = "technical"
    RISK = "risk"
    LIQUID = "liquid"
    INSIDER = "insider"   # Congressional trading intelligence
    SQUEEZE = "squeeze"   # FINRA short volume squeeze detector
    MACRO   = "macro"     # AI-Trader market-intel macro signals (BTC/QQQ/XLP/GLD/UUP)


class RunMode(str, Enum):
    BACKTEST = "backtest"
    LIVE = "live"
