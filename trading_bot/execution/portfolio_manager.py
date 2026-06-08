"""Portfolio Manager — orchestrator / execution brain.

Pipeline per ticker:
1. Run Fundamental, Vision, Technical, Liquid (opt), Social (opt), Risk CONCURRENTLY.
2. Blend directional scores by configured weights → composite [1, 100].
3. Map composite to LONG / SHORT / PASS via thresholds.
4. Apply Risk veto and minimum-risk-score gate.
5. Build concrete plan, size the order, route bracket through the broker.
6. Optionally publish the decision to AI4Trade.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Sequence

from config.settings import DecisionThresholds, Settings
from core.enums import AgentRole, Decision, OrderSide
from core.models import AgentEvaluation, AnalysisContext, TradeDecision
from agents.fundamental_agent import FundamentalAgent
from agents.liquid_agent import LiquidAgent
from agents.regime_agent import MarketRegime, RegimeSnapshot
from agents.risk_agent import RiskAgent
from agents.social_agent import SocialSentimentAgent
from agents.technical_agent import TechnicalAgent
from agents.vision_agent import VisionAgent
from execution.base_broker import BaseBroker, OrderReceipt

logger = logging.getLogger(__name__)


class PortfolioManager:
    def __init__(
        self,
        *,
        settings: Settings,
        broker: Optional[BaseBroker],
        fundamental: FundamentalAgent,
        vision: Optional[VisionAgent] = None,
        technical: TechnicalAgent = None,
        risk: RiskAgent,
        liquid: Optional[LiquidAgent] = None,
        social: Optional[SocialSentimentAgent] = None,
        publisher=None,   # Optional[SignalPublisher] — avoid circular import
    ) -> None:
        self.settings = settings
        self.broker = broker
        self.fundamental = fundamental
        self.vision = vision
        self.technical = technical
        self.risk = risk
        self.liquid = liquid
        self.social = social
        self.publisher = publisher
        self._weights = settings.weights.as_map()
        self._thresholds: DecisionThresholds = settings.thresholds
        self._regime: Optional[RegimeSnapshot] = None   # set via set_regime()

    def set_regime(self, regime: RegimeSnapshot) -> None:
        """Inject the current market regime (called once per scan cycle)."""
        self._regime = regime
        logger.info("Regime applied: %s (Δlong=%+.0f Δshort=%+.0f)",
                    regime.regime.value, regime.long_delta, regime.short_delta)

    async def decide(self, ctx: AnalysisContext) -> TradeDecision:
        coros = [
            self.fundamental.safe_evaluate(ctx),
            self.technical.safe_evaluate(ctx),
            self.risk.safe_evaluate(ctx),
        ]
        # Optional agents
        vision_idx = liquid_idx = social_idx = None
        if self.vision is not None:
            vision_idx = len(coros)
            coros.append(self.vision.safe_evaluate(ctx))
        if self.liquid is not None:
            liquid_idx = len(coros)
            coros.append(self.liquid.safe_evaluate(ctx))
        if self.social is not None:
            social_idx = len(coros)
            coros.append(self.social.safe_evaluate(ctx))

        results = await asyncio.gather(*coros)
        fundamental = results[0]
        technical   = results[1]
        risk        = results[2]
        vision_eval:  Optional[AgentEvaluation] = results[vision_idx]  if vision_idx  is not None else None
        liquid_eval:  Optional[AgentEvaluation] = results[liquid_idx]  if liquid_idx  is not None else None
        social_eval:  Optional[AgentEvaluation] = results[social_idx]  if social_idx  is not None else None

        evaluations = tuple(r for r in results)
        composite = self._composite(fundamental, vision_eval, technical, liquid_eval, social_eval)

        # Research #4: extract retail-driven surcharge from TechnicalAgent data
        retail_surcharge = 0.0
        if technical is not None and technical.data:
            retail_surcharge = float(technical.data.get("retail_surcharge", 0.0))

        decision = self._direction(composite, retail_surcharge=retail_surcharge)

        if risk.veto:
            logger.info("%s vetoed by Risk: %s", ctx.ticker, risk.rationale)
            decision = Decision.PASS
        elif risk.score < self._thresholds.min_risk_score:
            logger.info("%s blocked: risk score %d < %d",
                        ctx.ticker, risk.score, self._thresholds.min_risk_score)
            decision = Decision.PASS

        if decision is Decision.PASS:
            return TradeDecision(
                ticker=ctx.ticker, decision=Decision.PASS,
                composite_score=composite, evaluations=evaluations,
            )

        plan = self.risk.build_plan(ctx, intended=decision)
        if plan is None or plan.qty <= 0 or plan.risk_reward < self.settings.risk.min_risk_reward:
            logger.info("%s downgraded to PASS: no viable plan", ctx.ticker)
            return TradeDecision(
                ticker=ctx.ticker, decision=Decision.PASS,
                composite_score=composite, evaluations=evaluations,
            )

        side = OrderSide.BUY if decision is Decision.LONG else OrderSide.SELL
        return TradeDecision(
            ticker=ctx.ticker, decision=decision, composite_score=composite,
            side=side, risk=plan, evaluations=evaluations,
        )

    async def execute(self, decision: TradeDecision) -> Optional[OrderReceipt]:
        if not decision.is_actionable or self.broker is None:
            return None
        receipt = await self.broker.submit_bracket(decision)
        if receipt:
            logger.info("ORDER %s %s -> %s (%s)",
                        decision.decision.value, decision.ticker,
                        receipt.order_id, receipt.status)
        return receipt

    async def run_once(self, ctx: AnalysisContext, *, execute: bool = True) -> TradeDecision:
        decision = await self.decide(ctx)
        if execute and decision.is_actionable:
            await self.execute(decision)
        if self.publisher and self.settings.ai4trade_publish:
            await self.publisher.publish(decision)
        return decision

    def _composite(
        self,
        f: AgentEvaluation,
        v: AgentEvaluation,
        t: AgentEvaluation,
        liquid: Optional[AgentEvaluation],
        social: Optional[AgentEvaluation],
    ) -> float:
        agents = [
            ("fundamental", f),
            ("vision", v),
            ("technical", t),
        ]
        if liquid is not None:
            agents.append(("liquid", liquid))
        if social is not None:
            agents.append(("social", social))

        num = den = 0.0
        for key, ev in agents:
            if ev is None:          # optional agent not wired up (e.g. vision in backtest)
                continue
            w = self._weights.get(key, 0.0) * max(ev.confidence, 0.05)
            num += ev.score * w
            den += w
        return round(num / den, 2) if den else 50.0

    def _direction(
        self,
        composite: float,
        *,
        retail_surcharge: float = 0.0,
    ) -> Decision:
        """Map composite score to direction, applying regime threshold shifts.

        Research #4 (Gao et al.): retail-attention-driven momentum gets an extra
        +surcharge on the entry threshold. These setups are exploitable intraday
        (we're already day-trade-only) but require higher conviction to enter.
        """
        long_thr  = self._thresholds.long_above  + retail_surcharge
        short_thr = self._thresholds.short_below - retail_surcharge
        if self._regime is not None:
            long_thr  += self._regime.long_delta
            short_thr += self._regime.short_delta
        if composite >= long_thr:
            return Decision.LONG
        if composite <= short_thr:
            return Decision.SHORT
        return Decision.PASS

    @staticmethod
    def summarise(evaluations: Sequence[AgentEvaluation]) -> str:
        return " | ".join(
            f"{e.role.value}:{e.score}({e.confidence:.2f})" for e in evaluations
        )
