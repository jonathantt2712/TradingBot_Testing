"""Chief Risk Officer — volatility, sizing, SL/TP, and veto authority.

Produces both a 1..100 viability score AND a concrete ``RiskParameters``
trade plan. Sets ``veto=True`` when the trade is structurally unsound
(insufficient data, R/R below floor, sizing rounds to zero, etc.). The
Portfolio Manager treats a veto as an absolute block.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from config.settings import RiskConfig
from core.base_agent import BaseAgent, clamp_score
from core.enums import AgentRole, Decision
from core.models import AgentEvaluation, AnalysisContext, RiskParameters

logger = logging.getLogger(__name__)


class RiskAgent(BaseAgent):
    role = AgentRole.RISK

    def __init__(self, cfg: RiskConfig, *, weight: float = 0.0) -> None:
        # weight 0: risk is a gate/multiplier, not part of the directional blend.
        super().__init__(weight=weight)
        self.cfg = cfg

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        # FIX: was hardcoded to LONG; now evaluates the most viable direction
        # by building both plans and picking the one with the better R/R.
        long_plan = self.build_plan(ctx, intended=Decision.LONG)
        short_plan = self.build_plan(ctx, intended=Decision.SHORT)

        # Pick whichever direction has a valid plan (prefer LONG on tie)
        plan = long_plan
        if plan is None or (short_plan is not None and short_plan.risk_reward > (plan.risk_reward if plan else 0)):
            plan = short_plan

        if plan is None:
            return AgentEvaluation(
                role=self.role,
                score=1,
                veto=True,
                rationale="cannot build a valid risk plan",
            )

        score = self._viability_score(plan, ctx)
        veto = plan.risk_reward < self.cfg.min_risk_reward or plan.qty <= 0
        return AgentEvaluation(
            role=self.role,
            score=clamp_score(score),
            confidence=0.9,
            veto=veto,
            rationale=(
                f"R/R={plan.risk_reward:.2f} qty={plan.qty:g} "
                f"SL={plan.stop_loss:.2f} TP={plan.take_profit:.2f}"
                + (" VETO" if veto else "")
            ),
            data={"plan": plan},
        )

    # --- planning -------------------------------------------------------

    def build_plan(self, ctx: AnalysisContext, *, intended: Decision) -> Optional[RiskParameters]:
        if ctx.bars is None or ctx.bars.empty:
            return None
        price = ctx.last_price
        if price is None or price <= 0:
            return None

        atr = self._atr(ctx.bars)
        if atr <= 0:
            return None

        equity = float(ctx.account.get("equity", 0.0))
        if equity <= 0:
            logger.warning("no account equity in context; using nominal $100k for sizing")
            equity = 100_000.0

        risk_usd = equity * self.cfg.max_risk_per_trade_pct
        stop_dist = atr * self.cfg.atr_stop_multiple
        target_dist = atr * self.cfg.atr_target_multiple

        if intended is Decision.LONG:
            stop = price - stop_dist
            target = price + target_dist
        else:  # SHORT
            stop = price + stop_dist
            target = price - target_dist

        per_share_risk = abs(price - stop)
        if per_share_risk <= 0:
            return None

        qty = risk_usd / per_share_risk
        max_qty_by_exposure = (equity * self.cfg.max_position_pct) / price
        qty = float(np.floor(min(qty, max_qty_by_exposure)))

        rr = target_dist / stop_dist if stop_dist > 0 else 0.0
        return RiskParameters(
            qty=qty,
            entry=price,
            stop_loss=round(stop, 4),
            take_profit=round(target, 4),
            risk_reward=round(rr, 3),
            risk_per_trade_usd=round(risk_usd, 2),
        )

    def _viability_score(self, plan: RiskParameters, ctx: AnalysisContext) -> float:
        rr_score = np.interp(plan.risk_reward, [1.0, self.cfg.min_risk_reward, 3.0], [20, 55, 95])
        size_ok = 60.0 if plan.qty > 0 else 1.0
        vol = self._atr(ctx.bars) / max(ctx.last_price or 1.0, 1e-9)
        vol_score = float(np.interp(vol, [0.005, 0.03, 0.08], [85, 55, 15]))
        return float(np.mean([rr_score, size_ok, vol_score]))

    @staticmethod
    def _atr(bars: pd.DataFrame, length: int = 14) -> float:
        high, low, close = bars["high"], bars["low"], bars["close"]
        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(length).mean().iloc[-1]
        return float(atr) if not np.isnan(atr) else 0.0
