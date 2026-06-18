"""Social Sentiment Agent — AI4Trade community signal feed.

Reads the ai4trade.ai signal feed for a given ticker and converts the
collective intelligence of other AI trading agents into a 1..100 directional
score.

Scoring logic:
  * Action signals (position/trade): buy/cover = bullish, sell/short = bearish.
    Weighted by recency (exponential decay) and how recently the signal fired.
  * Strategy signals: presence of bull/bear keywords in title + content.
  * Confidence scales with signal volume — few signals → low confidence.

Degrades gracefully to NEUTRAL if the feed is unavailable or returns no
signals for the ticker.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from core.base_agent import NEUTRAL_SCORE, BaseAgent, clamp_score
from core.enums import AgentRole
from core.models import AgentEvaluation, AnalysisContext
from data.ai4trade_client import AI4TradeClient

logger = logging.getLogger(__name__)

_BULL_ACTIONS = {"buy", "cover"}
_BEAR_ACTIONS = {"sell", "short"}
_BULL_WORDS = {"bull", "long", "buy", "breakout", "surge", "rally", "upside", "bullish", "growth", "beat"}
_BEAR_WORDS = {"bear", "short", "sell", "breakdown", "plunge", "downside", "bearish", "miss", "warning"}


def _recency_weight(signal: dict, now: datetime, half_life_hours: float = 24.0) -> float:
    """Exponential decay weight — signals older than half_life_hours contribute half."""
    ts_raw = signal.get("timestamp") or signal.get("created_at") or signal.get("last_reply_at")
    if not ts_raw:
        return 0.5
    try:
        if isinstance(ts_raw, (int, float)):
            ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
        else:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        age_hours = (now - ts).total_seconds() / 3600
        return math.exp(-age_hours * math.log(2) / half_life_hours)
    except Exception:
        return 0.5


class SocialSentimentAgent(BaseAgent):
    """5th directional agent using AI4Trade community signal feed."""

    role = AgentRole.SOCIAL

    def __init__(
        self,
        client: AI4TradeClient,
        *,
        weight: float = 0.15,
        max_signals: int = 30,
    ) -> None:
        super().__init__(weight=weight)
        self.client = client
        self.max_signals = max_signals

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        if ctx.backtest_mode:
            # The AI4Trade feed reports the CURRENT community state; replaying it onto
            # historical windows would leak future information (look-ahead bias).
            return AgentEvaluation(
                role=self.role, score=NEUTRAL_SCORE, confidence=0.0,
                rationale="social: neutral in backtest (point-in-time data, no look-ahead)",
            )
        signals = await self.client.get_signal_feed(
            symbol=ctx.ticker,
            limit=self.max_signals,
            sort="active",
        )

        if not signals:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.05,
                rationale="no community signals found",
            )

        now = datetime.now(tz=timezone.utc)
        bull_w = bear_w = 0.0
        n_action = n_strategy = 0

        for sig in signals:
            w = _recency_weight(sig, now)
            sig_type = sig.get("type", "")
            action = (sig.get("action") or sig.get("side") or "").lower()

            if sig_type in ("position", "trade") and action:
                if action in _BULL_ACTIONS:
                    bull_w += w
                elif action in _BEAR_ACTIONS:
                    bear_w += w
                n_action += 1

            elif sig_type in ("strategy", "discussion"):
                text = f"{sig.get('title', '')} {sig.get('content', '')}".lower()
                bull_hits = sum(1 for w_ in _BULL_WORDS if w_ in text)
                bear_hits = sum(1 for w_ in _BEAR_WORDS if w_ in text)
                bull_w += bull_hits * w * 0.5   # strategy signals half-weighted vs actions
                bear_w += bear_hits * w * 0.5
                n_strategy += 1

        total_w = bull_w + bear_w
        if total_w < 1e-9:
            score = NEUTRAL_SCORE
            rationale = f"no directional signals ({len(signals)} signals parsed)"
            sentiment_ratio = 0.5
        else:
            sentiment_ratio = bull_w / total_w
            score = clamp_score(1 + sentiment_ratio * 99)
            rationale = (
                f"bull_w={bull_w:.1f} bear_w={bear_w:.1f} "
                f"({n_action} trades, {n_strategy} strategies)"
            )

        # Minimum 3 signals required for confidence above 0.40
        base_conf = 0.1 + 0.03 * len(signals)
        if len(signals) < 3:
            base_conf = min(base_conf, 0.35)
        confidence = min(0.90, base_conf)   # universal 0.90 cap

        return AgentEvaluation(
            role=self.role,
            score=score,
            confidence=confidence,
            rationale=rationale,
            data={"n_signals": len(signals), "bull_w": round(bull_w, 2), "bear_w": round(bear_w, 2)},
            reasoning={
                "signals_analyzed": len(signals),
                "trade_signals": n_action,
                "strategy_signals": n_strategy,
                "bull_weight": round(bull_w, 2),
                "bear_weight": round(bear_w, 2),
                "sentiment_ratio": round(sentiment_ratio, 3),
                "note": (
                    "Signals sourced from AI4Trade community feed. "
                    "Trade signals (position/trade) are full-weight; "
                    "strategy/discussion signals are half-weight. "
                    "All signals decay exponentially with a 24h half-life."
                ),
            },
        )
