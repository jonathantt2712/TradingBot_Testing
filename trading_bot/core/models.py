"""Core domain models shared across all agents and the portfolio manager."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence, Tuple

import pandas as pd

from core.enums import AgentRole, Decision, OrderSide


# ── Agent evaluation ──────────────────────────────────────────────────────────

@dataclass
class AgentEvaluation:
    """Single agent's verdict: directional score + confidence + optional veto."""
    role:       AgentRole
    score:      float              # 1..100 — 1=max bearish, 50=neutral, 100=max bullish
    confidence: float = 0.7        # 0..1 — how reliable this score is
    rationale:  str   = ""         # human-readable explanation
    veto:       bool  = False      # True → PortfolioManager ignores all other scores
    data:       Optional[Any] = None  # extra structured data (signals dict, plan, etc.)


# ── Risk plan ─────────────────────────────────────────────────────────────────

@dataclass
class RiskParameters:
    """Concrete trade plan produced by the RiskAgent."""
    qty:                float   # number of shares
    entry:              float   # expected fill price
    stop_loss:          float   # hard stop
    take_profit:        float   # profit target
    risk_reward:        float   # take_profit_dist / stop_dist
    risk_per_trade_usd: float = 0.0  # dollar amount risked


# ── Analysis context ──────────────────────────────────────────────────────────

@dataclass
class AnalysisContext:
    """Everything agents need to evaluate a single ticker."""
    ticker:           str
    bars:             Optional[pd.DataFrame] = None   # OHLCV, DatetimeIndex
    account:          dict                   = field(default_factory=dict)
    chart_image_path: Optional[str]          = None   # path to rendered PNG
    as_of:            Optional[pd.Timestamp] = None   # evaluation time (backtests)

    @property
    def last_price(self) -> Optional[float]:
        if self.bars is None or self.bars.empty:
            return None
        return float(self.bars["close"].iloc[-1])


# ── Trade decision ────────────────────────────────────────────────────────────

@dataclass
class TradeDecision:
    """Final output of the PortfolioManager for one ticker."""
    ticker:          str
    decision:        Decision
    composite_score: float
    evaluations:     Tuple[AgentEvaluation, ...]  = field(default_factory=tuple)
    side:            Optional[OrderSide]          = None
    risk:            Optional[RiskParameters]     = None

    @property
    def is_actionable(self) -> bool:
        return self.decision is not Decision.PASS and self.risk is not None
