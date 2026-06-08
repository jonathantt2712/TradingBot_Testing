"""Signal Publisher — syncs bot decisions to AI4Trade.

After each actionable trade decision, publishes a signal to ai4trade.ai so:
  1. The bot builds a public track record with verifiable P&L.
  2. Other agents can follow/copy the bot's trades.
  3. Community replies provide external validation.

Also publishes PASS decisions as strategy analysis (non-trade discussion)
when the composite score is close to a threshold — useful for building
reputation even on non-trades.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.enums import Decision
from core.models import TradeDecision
from data.ai4trade_client import AI4TradeClient

logger = logging.getLogger(__name__)

# Symbols Liquid/AI4Trade recognise as crypto vs US stock
_CRYPTO = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "MATIC", "LINK", "UNI", "AAVE", "XRP"}


def _market(ticker: str) -> str:
    return "crypto" if ticker.upper() in _CRYPTO else "us-stock"


def _action(decision: TradeDecision) -> str:
    if decision.decision is Decision.LONG:
        return "buy"
    if decision.decision is Decision.SHORT:
        return "short"
    return ""


def _build_content(decision: TradeDecision) -> str:
    """Human-readable rationale to attach to the published signal."""
    lines = [
        f"Composite score: {decision.composite_score:.1f}/100",
    ]
    for ev in decision.evaluations:
        lines.append(f"  {ev.role.value}: {ev.score} (conf={ev.confidence:.0%}) — {ev.rationale}")
    if decision.risk:
        r = decision.risk
        lines.append(
            f"Plan: entry={r.entry:.2f} SL={r.stop_loss:.2f} TP={r.take_profit:.2f} "
            f"qty={r.qty:g} R/R={r.risk_reward:.2f}"
        )
    return "\n".join(lines)


class SignalPublisher:
    """Publishes bot decisions to AI4Trade after each evaluation cycle."""

    def __init__(
        self,
        client: AI4TradeClient,
        *,
        publish_pass: bool = False,  # also publish non-trade strategy posts
        min_score_for_strategy: float = 55.0,
    ) -> None:
        self.client = client
        self.publish_pass = publish_pass
        self.min_score_for_strategy = min_score_for_strategy

    async def publish(self, decision: TradeDecision) -> None:
        if not self.client.token:
            return  # no auth — silently skip

        ticker = decision.ticker
        now = datetime.now(tz=timezone.utc).isoformat()

        if decision.is_actionable and decision.risk:
            # Publish a live trade signal
            r = decision.risk
            result = await self.client.publish_trade(
                market=_market(ticker),
                action=_action(decision),
                symbol=ticker,
                price=r.entry,
                quantity=r.qty,
                content=_build_content(decision),
                executed_at=now,
            )
            if result.get("success") or result.get("id"):
                logger.info("AI4Trade: published %s %s signal", decision.decision.value, ticker)
            else:
                logger.debug("AI4Trade publish trade returned: %s", result)

        elif self.publish_pass and decision.composite_score >= self.min_score_for_strategy:
            # Publish a near-miss as a strategy/analysis post
            direction = "bullish" if decision.composite_score >= 55 else "bearish"
            result = await self.client.publish_strategy(
                market=_market(ticker),
                title=f"{direction.capitalize()} on {ticker} (score {decision.composite_score:.0f})",
                content=_build_content(decision),
                symbols=[ticker],
                tags=[ticker.lower(), direction, "ai-analysis"],
            )
            if result.get("success") or result.get("id"):
                logger.info("AI4Trade: published %s strategy for %s", direction, ticker)
