"""Chief Risk Officer — volatility, sizing, SL/TP, and veto authority.

Produces both a 1..100 viability score AND a concrete ``RiskParameters``
trade plan. Sets ``veto=True`` when the trade is structurally unsound
(insufficient data, R/R below floor, sizing rounds to zero, etc.). The
Portfolio Manager treats a veto as an absolute block.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from config.settings import RiskConfig
from core import health
from core.base_agent import BaseAgent, clamp_score
from core.enums import AgentRole, Decision
from core.freshness import bar_staleness
from core.models import AgentEvaluation, AnalysisContext, RiskParameters

_STRATEGY_WEIGHTS_FILE = Path(__file__).parent.parent / "data" / "strategy_weights.json"

logger = logging.getLogger(__name__)


class RiskAgent(BaseAgent):
    role = AgentRole.RISK

    def __init__(self, cfg: RiskConfig, *, weight: float = 0.0) -> None:
        # weight 0: risk is a gate/multiplier, not part of the directional blend.
        super().__init__(weight=weight)
        self.cfg = cfg

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        # Fail closed on stale data: a halt/feed-gap/weekend snapshot would have
        # us size against a price that no longer exists. Skip in backtests, whose
        # historical bars are "stale" only by wall-clock definition.
        if not getattr(ctx, "backtest_mode", False):
            stale, reason = bar_staleness(ctx.bars, max_age_factor=self.cfg.max_bar_age_factor)
            if stale:
                return AgentEvaluation(
                    role=self.role,
                    score=1,
                    veto=True,
                    rationale=f"stale data — {reason}",
                    reasoning={"veto": True, "veto_reason": f"stale data — {reason}"},
                )

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

        veto_reason: Optional[str] = None
        if plan.qty <= 0:
            veto_reason = "Position size rounds to zero — trade not viable at current equity/price"
        elif plan.risk_reward < self.cfg.min_risk_reward:
            veto_reason = (
                f"R/R {plan.risk_reward:.2f} below minimum {self.cfg.min_risk_reward:.1f} — "
                "not enough reward for the risk taken"
            )

        atr = self._atr(ctx.bars)
        price = float(ctx.last_price or 0.0)
        equity = float(ctx.account.get("equity", 0.0))

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
            reasoning={
                "veto": veto,
                "veto_reason": veto_reason,
                "plan": {
                    "entry":              round(plan.entry, 4),
                    "stop_loss":          round(plan.stop_loss, 4),
                    "take_profit":        round(plan.take_profit, 4),
                    "qty":                plan.qty,
                    "risk_reward":        round(plan.risk_reward, 3),
                    "risk_per_trade_usd": round(plan.risk_per_trade_usd, 2),
                },
                "sizing": {
                    "account_equity":        round(equity, 2),
                    "max_risk_pct":          self.cfg.max_risk_per_trade_pct,
                    "risk_usd":              round(plan.risk_per_trade_usd, 2),
                    "max_position_pct":      self.cfg.max_position_pct,
                    "atr":                   round(atr, 4),
                    "atr_pct":               round(atr / max(price, 0.01) * 100, 3),
                    "volatility_multiplier": round(self._volatility_multiplier(atr, price), 3),
                    "atr_stop_multiple":     self.cfg.atr_stop_multiple,
                    "atr_target_multiple":   self.cfg.atr_target_multiple,
                },
                "thresholds": {
                    "min_risk_reward":    self.cfg.min_risk_reward,
                    "max_open_positions": self.cfg.max_open_positions,
                    "max_daily_loss_pct": self.cfg.max_daily_loss_pct,
                },
                "note": (
                    "Stop distance = ATR × stop_multiple. "
                    "Target capped at session high (LONG) or low (SHORT) to keep R/R variable. "
                    "Position sized at 1% equity risk per trade, capped at 20% of equity."
                ),
            },
        )

    # --- planning -------------------------------------------------------

    def _effective_atr_multiples(self, backtest_mode: bool) -> tuple[float, float]:
        """Live ATR stop/target multiples.

        In LIVE trading, strategy_weights.json — written by the self-tuner and by
        the optimizer's "Apply Optimal Params" action — overrides the configured
        (env) values so tuning takes effect WITHOUT a redeploy. In backtests the
        configured values are used verbatim, so the optimizer's per-combo ATR
        params are never masked by the live file.
        """
        stop, target = self.cfg.atr_stop_multiple, self.cfg.atr_target_multiple
        if backtest_mode:
            return stop, target
        try:
            with open(_STRATEGY_WEIGHTS_FILE, encoding="utf-8") as f:
                w = json.load(f)
            # Only honor the file when tuning has been DELIBERATELY activated
            # (optimizer Apply or an active self-tune). Otherwise fall back to the
            # configured env values so baked-in defaults never shift live sizing.
            if w.get("live_tuning_active"):
                stop   = float(w.get("atr_stop_multiple", stop))
                target = float(w.get("atr_target_multiple", target))
        except Exception:
            pass
        return stop, target

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
            # Fail closed: without verified equity we cannot size a position.
            # (A broker API hiccup must never trade against fabricated capital.)
            # This is why Risk shows a flat veto for every ticker — surface it.
            if not getattr(ctx, "backtest_mode", False):
                health.report_issue(
                    "risk:no_equity",
                    "RiskAgent can't size positions — no verified account equity.",
                    remediation="Connect a funded/paper Alpaca account "
                                "(ALPACA_API_KEY_ID / ALPACA_API_SECRET); until then every "
                                "trade is vetoed.",
                )
            logger.error("no account equity in context — refusing to build a plan")
            return None

        stop_mult, target_mult = self._effective_atr_multiples(getattr(ctx, "backtest_mode", False))
        vol_mult = self._volatility_multiplier(atr, price)
        kelly_mult = self._kelly_multiplier(target_mult / max(stop_mult, 0.1))
        risk_usd = equity * self.cfg.max_risk_per_trade_pct * vol_mult * kelly_mult
        stop_dist = atr * stop_mult
        target_dist = self._target_dist(ctx.bars, intended, price, atr, target_mult)
        if target_dist <= 0:
            return None

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

    @staticmethod
    def _kelly_multiplier(rr: float) -> float:
        """Fractional Kelly position sizing multiplier.

        Reads win_rate_30d and update_count from strategy_weights.json.
        Half Kelly when N >= 30 trades, Quarter Kelly (cap at 1.0x) when N < 30.
        Normalised so that W=50%, R=2.0 yields 1.0x (baseline unchanged).
        """
        try:
            with open(_STRATEGY_WEIGHTS_FILE, encoding="utf-8") as f:
                w = json.load(f)
            win_rate_pct = w.get("win_rate_30d")
            update_count = int(w.get("update_count", 0))
            if win_rate_pct is None or rr <= 0:
                return 1.0
            W = win_rate_pct / 100.0
            K = W - (1.0 - W) / rr
            if K <= 0:
                return 0.25  # negative Kelly → quarter size
            K_neutral = 0.5 - 0.5 / 2.0  # = 0.25 at W=50%, R=2.0
            kelly_mult = K / K_neutral
            if update_count < 30:
                kelly_mult = min(kelly_mult, 1.0)  # no size-up without track record
            return float(np.clip(kelly_mult, 0.25, 2.0))
        except Exception:
            return 1.0

    @staticmethod
    def _volatility_multiplier(atr: float, price: float) -> float:
        """Scale position size inversely to ATR/price ratio.

        Baseline ATR% = 1.5% (moderate volatility → 1.0x size).
        High ATR% (>4%) → smaller position (floor 0.5x).
        Low ATR% (<0.5%) → larger position (cap 1.5x).
        """
        if price <= 0 or atr <= 0:
            return 1.0
        atr_pct = atr / price
        # Normalize: 1.5% ATR = neutral (1.0x), scale inversely
        multiplier = 0.015 / atr_pct
        return float(np.clip(multiplier, 0.5, 1.5))

    def _target_dist(
        self,
        bars: pd.DataFrame,
        intended: Decision,
        price: float,
        atr: float,
        target_mult: Optional[float] = None,
    ) -> float:
        """Target distance capped by intraday structure.

        The ATR multiple alone makes R/R a constant (target_mult / stop_mult),
        which turns the min-R/R gate into a no-op. Instead, cap the target at
        the session high (LONG) / session low (SHORT): if there is little room
        before hitting the level, R/R drops and the gate can reject the trade.
        When price is already at/through the level (breakout territory, within
        0.25×ATR), there is no overhead structure — use the full ATR target.
        """
        atr_dist = atr * (target_mult if target_mult is not None else self.cfg.atr_target_multiple)

        session = bars
        if hasattr(bars.index, "date") and len(bars.index) > 0:
            today = bars.index[-1].date()
            today_df = bars[bars.index.map(lambda x: x.date()) == today]
            if not today_df.empty:
                session = today_df

        if intended is Decision.LONG:
            room = float(session["high"].max()) - price
        else:
            room = price - float(session["low"].min())

        if room <= atr * 0.25:          # at/through the level → breakout, full target
            return atr_dist
        return min(atr_dist, room)

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
