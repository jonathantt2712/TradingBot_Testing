"""Portfolio Manager -- orchestrator / execution brain.

Pipeline per ticker:
1. Run Fundamental, Vision, Technical, Liquid (opt), Social (opt), Risk CONCURRENTLY.
2. Blend directional scores by configured weights -> composite [1, 100].
3. Map composite to LONG / SHORT / PASS via thresholds.
4. Apply Risk veto and minimum-risk-score gate.
5. Build concrete plan, size the order, route bracket through the broker.
6. Optionally publish the decision to AI4Trade.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from config.settings import DecisionThresholds, Settings
from core.enums import AgentRole, Decision, OrderSide
from core.models import AgentEvaluation, AnalysisContext, TradeDecision
from agents.fundamental_agent import FundamentalAgent
from agents.liquid_agent import LiquidAgent
from agents.regime_agent import MarketRegime, RegimeSnapshot
from agents.risk_agent import RiskAgent
from agents.insider_agent import InsiderAgent
from agents.social_agent import SocialSentimentAgent
from agents.macro_agent import MacroSignalAgent
from agents.squeeze_agent import SqueezeAgent
from agents.technical_agent import TechnicalAgent
from agents.decision_agent import DecisionAgent
from agents.vision_agent import VisionAgent
from execution.base_broker import BaseBroker, OrderReceipt

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Audit trail: one JSON line per decision/fill, next to the other debug logs.
_AUDIT_FILE = Path(__file__).parents[2] / "logs" / "decisions.jsonl"

# Runtime tuning file — written by the self-tuner and the optimizer's Apply action.
_WEIGHTS_FILE = Path(__file__).parents[1] / "data" / "strategy_weights.json"


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
        insider: Optional["InsiderAgent"] = None,
        squeeze: Optional["SqueezeAgent"] = None,
        macro: Optional["MacroSignalAgent"] = None,
        publisher=None,
        decision_agent: Optional[DecisionAgent] = None,
    ) -> None:
        self.settings = settings
        self.broker = broker
        self.fundamental = fundamental
        self.vision = vision
        self.technical = technical
        self.risk = risk
        self.liquid = liquid
        self.social = social
        self.insider = insider
        self.squeeze = squeeze
        self.macro   = macro
        self.publisher = publisher
        self._decision_agent = decision_agent
        self._weights = settings.weights.as_map()
        self._thresholds: DecisionThresholds = settings.thresholds
        self._regime: Optional[RegimeSnapshot] = None

        # Daily-loss kill switch state (reset each ET trading day)
        self._day_start_equity: Optional[float] = None
        self._kill_switch_date: Optional[date] = None
        self._halted: bool = False

        # Strong refs to fire-and-forget tasks (else the event loop may GC them)
        self._bg_tasks: set = set()

    def set_regime(self, regime: RegimeSnapshot) -> None:
        """Inject the current market regime (called once per scan cycle)."""
        self._regime = regime
        logger.info("Regime applied: %s (Dlong=%+.0f Dshort=%+.0f)",
                    regime.regime.value, regime.long_delta, regime.short_delta)

    async def decide(self, ctx: AnalysisContext) -> TradeDecision:
        coros = [
            self.fundamental.safe_evaluate(ctx),
            self.technical.safe_evaluate(ctx),
            self.risk.safe_evaluate(ctx),
        ]
        vision_idx = liquid_idx = social_idx = squeeze_idx = None
        if self.vision is not None:
            vision_idx = len(coros)
            coros.append(self.vision.safe_evaluate(ctx))
        if self.liquid is not None:
            liquid_idx = len(coros)
            coros.append(self.liquid.safe_evaluate(ctx))
        if self.social is not None:
            social_idx = len(coros)
            coros.append(self.social.safe_evaluate(ctx))
        insider_idx = None
        if self.insider is not None:
            insider_idx = len(coros)
            coros.append(self.insider.safe_evaluate(ctx))
        if self.squeeze is not None:
            squeeze_idx = len(coros)
            coros.append(self.squeeze.safe_evaluate(ctx))
        macro_idx = None
        if self.macro is not None:
            macro_idx = len(coros)
            coros.append(self.macro.safe_evaluate(ctx))

        results = await asyncio.gather(*coros)
        fundamental = results[0]
        technical   = results[1]
        risk        = results[2]
        vision_eval:  Optional[AgentEvaluation] = results[vision_idx]  if vision_idx  is not None else None
        liquid_eval:  Optional[AgentEvaluation] = results[liquid_idx]  if liquid_idx  is not None else None
        social_eval:  Optional[AgentEvaluation] = results[social_idx]  if social_idx  is not None else None
        insider_eval: Optional[AgentEvaluation] = results[insider_idx] if insider_idx is not None else None
        squeeze_eval: Optional[AgentEvaluation] = results[squeeze_idx] if squeeze_idx is not None else None
        macro_eval:   Optional[AgentEvaluation] = results[macro_idx]   if macro_idx   is not None else None

        evaluations = tuple(r for r in results)

        decision_meta: Optional[dict] = None
        if self._decision_agent is not None and self._decision_agent.available:
            regime_value = self._regime.regime.value if self._regime else "neutral"
            regime_rationale = getattr(self._regime, "rationale", "") if self._regime else ""
            vix_level = getattr(self._regime, "vix_level", None) if self._regime else None
            if vix_level is not None:
                regime_rationale = f"{regime_rationale} | VIX={vix_level:.1f}"
            all_evals = [ev for ev in evaluations if ev is not None]
            decision, composite, decision_meta = await self._decision_agent.decide(
                ctx, all_evals, regime_value, regime_rationale,
            )
        else:
            composite = self._composite(fundamental, vision_eval, technical, liquid_eval, social_eval, insider_eval, squeeze_eval, macro_eval)
            retail_surcharge = 0.0
            if technical is not None and technical.data:
                retail_surcharge = float(technical.data.get("retail_surcharge", 0.0))
            long_base, short_base = self._effective_thresholds(getattr(ctx, "backtest_mode", False))
            decision = self._direction(composite, retail_surcharge=retail_surcharge,
                                       long_base=long_base, short_base=short_base)

        if risk.veto:
            logger.info("%s vetoed by Risk: %s", ctx.ticker, risk.rationale)
            decision = Decision.PASS
        elif risk.score < self._thresholds.min_risk_score:
            logger.info("%s blocked: risk score %d < %d",
                        ctx.ticker, risk.score, self._thresholds.min_risk_score)
            decision = Decision.PASS

        # ── Regime-aware LONG cooldown ────────────────────────────────────
        # Research: when macro regime is RISK_OFF (VIX>25 or SPY waterfall),
        # only allow LONG entries with very high conviction (composite >= 75).
        # Shorts are still permitted — they are directionally aligned in selloffs.
        if (
            decision is Decision.LONG
            and self._regime is not None
            and self._regime.regime.value == "risk_off"
            and composite < 75.0
        ):
            logger.info(
                "%s LONG blocked by RISK_OFF regime (composite=%.1f < 75)",
                ctx.ticker, composite,
            )
            decision = Decision.PASS

        if decision is Decision.PASS:
            return TradeDecision(
                ticker=ctx.ticker, decision=Decision.PASS,
                composite_score=composite, evaluations=evaluations,
                decision_meta=decision_meta,
            )

        plan = self.risk.build_plan(ctx, intended=decision)

        # VIX-aware position scaling: high volatility → smaller size
        if plan is not None and self._regime is not None:
            vix = self._regime.vix_level
            if vix is not None:
                if vix > 40:
                    plan.qty = float(int(plan.qty * 0.5))
                    logger.info("%s VIX=%.1f > 40: position scaled 50%%", ctx.ticker, vix)
                elif vix > 30:
                    plan.qty = float(int(plan.qty * 0.7))
                    logger.info("%s VIX=%.1f > 30: position scaled 70%%", ctx.ticker, vix)

        if plan is None or plan.qty <= 0 or plan.risk_reward < self.settings.risk.min_risk_reward:
            logger.info("%s downgraded to PASS: no viable plan", ctx.ticker)
            return TradeDecision(
                ticker=ctx.ticker, decision=Decision.PASS,
                composite_score=composite, evaluations=evaluations,
                decision_meta=decision_meta,
            )

        side = OrderSide.BUY if decision is Decision.LONG else OrderSide.SELL
        return TradeDecision(
            ticker=ctx.ticker, decision=decision, composite_score=composite,
            side=side, risk=plan, evaluations=evaluations,
            decision_meta=decision_meta,
        )

    def _check_daily_loss(self, account: dict) -> bool:
        """Update the kill switch from current equity. Returns True if halted.

        Tracks the first equity reading of each ET day as the baseline; once
        equity drops more than ``max_daily_loss_pct`` below it, all new entries
        are blocked until the next trading day.
        """
        equity = float(account.get("equity", 0.0) or 0.0)
        if equity <= 0:
            return self._halted  # unknown equity — leave state unchanged

        today = datetime.now(_ET).date()
        if self._kill_switch_date != today:
            self._kill_switch_date = today
            self._day_start_equity = equity
            self._halted = False
            return False

        limit = self._day_start_equity * (1.0 - self.settings.risk.max_daily_loss_pct)
        if not self._halted and equity < limit:
            self._halted = True
            logger.error(
                "KILL SWITCH: equity %.2f below daily loss limit %.2f "
                "(start %.2f, max loss %.1f%%) — no new entries today",
                equity, limit, self._day_start_equity,
                self.settings.risk.max_daily_loss_pct * 100,
            )
        return self._halted

    async def _entry_allowed(self, ticker: str) -> bool:
        """Pre-trade gate: no duplicate exposure, respect max open positions.

        Fails closed — if portfolio state cannot be fetched, the entry is
        skipped rather than risked blind.
        """
        try:
            positions = await self.broker.get_positions()
            open_orders = await self.broker.get_open_orders()
        except Exception as exc:
            logger.error("%s entry blocked: cannot verify portfolio state (%s)", ticker, exc)
            return False

        symbol = ticker.upper()
        if any(p.get("symbol", "").upper() == symbol for p in positions):
            logger.info("%s entry skipped: position already open", ticker)
            return False
        if any(o.get("symbol", "").upper() == symbol for o in open_orders):
            logger.info("%s entry skipped: open order already working", ticker)
            return False
        if len(positions) >= self.settings.risk.max_open_positions:
            logger.info(
                "%s entry skipped: %d open positions >= max %d",
                ticker, len(positions), self.settings.risk.max_open_positions,
            )
            return False
        return True

    async def execute(self, decision: TradeDecision) -> Optional[OrderReceipt]:
        if not decision.is_actionable or self.broker is None:
            return None
        if self._halted:
            logger.warning("%s entry blocked: daily loss kill switch active", decision.ticker)
            return None
        if not await self._entry_allowed(decision.ticker):
            return None
        receipt = await self.broker.submit_bracket(decision)
        if receipt:
            logger.info("ORDER %s %s -> %s (%s)",
                        decision.decision.value, decision.ticker,
                        receipt.order_id, receipt.status)
            task = asyncio.create_task(self._track_fill(decision, receipt))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        return receipt

    async def run_once(self, ctx: AnalysisContext, *, execute: bool = True) -> TradeDecision:
        self._check_daily_loss(ctx.account)
        decision = await self.decide(ctx)
        receipt = None
        if execute and decision.is_actionable:
            receipt = await self.execute(decision)
        self._audit_decision(decision, executed=receipt is not None, receipt=receipt)
        if self.publisher and self.settings.ai4trade_publish:
            await self.publisher.publish(decision)
        return decision

    # ── Audit trail (trade forensics) ─────────────────────────────────────

    def _audit_decision(
        self,
        decision: TradeDecision,
        *,
        executed: bool,
        receipt: Optional[OrderReceipt],
    ) -> None:
        """Append the full decision (scores, rationales, plan) to decisions.jsonl.

        Answers "why did the bot (not) trade X at time T?" after the fact.
        Must never interfere with trading — failures are swallowed.
        """
        r = decision.risk
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "decision",
            "ticker": decision.ticker,
            "decision": decision.decision.value,
            "composite": decision.composite_score,
            "halted": self._halted,
            "regime": self._regime.regime.value if self._regime else None,
            "regime_weights": self._REGIME_MULTIPLIERS.get(
                self._regime.regime.value if self._regime else "neutral", {}
            ),
            "agents": [
                {
                    "role": e.role.value,
                    "score": e.score,
                    "confidence": round(e.confidence, 3),
                    "veto": e.veto,
                    "rationale": e.rationale,
                }
                for e in decision.evaluations
            ],
            "plan": {
                "entry": r.entry, "stop_loss": r.stop_loss, "take_profit": r.take_profit,
                "qty": r.qty, "risk_reward": r.risk_reward,
            } if r else None,
            "decision_agent": decision.decision_meta,
            "executed": executed,
            "order_id": receipt.order_id if receipt else None,
            "order_status": receipt.status if receipt else None,
        }
        self._append_audit(record)

    @staticmethod
    def _append_audit(record: dict) -> None:
        try:
            _AUDIT_FILE.parent.mkdir(exist_ok=True)
            with open(_AUDIT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception:
            logger.debug("audit write failed", exc_info=True)

    async def _track_fill(self, decision: TradeDecision, receipt: OrderReceipt) -> None:
        """Poll the entry order until filled and record realized slippage.

        Slippage = signed difference between the intended entry (decision time)
        and the actual fill — the bot's own execution-quality metric.
        """
        if self.broker is None or decision.risk is None:
            return
        try:
            for _ in range(12):  # up to ~60s
                await asyncio.sleep(5)
                order = await self.broker.get_order(receipt.order_id)
                if order is None:
                    return
                if order.get("status") == "filled" and order.get("filled_avg_price"):
                    fill = float(order["filled_avg_price"])
                    side = 1.0 if decision.decision is Decision.LONG else -1.0
                    slip = (fill - decision.risk.entry) * side  # positive = paid worse
                    self._append_audit({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "type": "fill",
                        "ticker": decision.ticker,
                        "order_id": receipt.order_id,
                        "intended_entry": decision.risk.entry,
                        "fill_price": fill,
                        "filled_qty": order.get("filled_qty"),
                        "slippage_per_share": round(slip, 4),
                        "slippage_bps": round(slip / decision.risk.entry * 10_000, 2),
                    })
                    logger.info("%s filled @ %.4f (slippage %+.4f/share, %+.1f bps)",
                                decision.ticker, fill, slip,
                                slip / decision.risk.entry * 10_000)
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("fill tracking failed for %s", decision.ticker, exc_info=True)

    # Regime-adaptive weight multipliers.
    # Values > 1.0 boost the agent's effective weight; < 1.0 reduces it.
    # Applied on top of the base weights — normalisation happens inside _composite.
    _REGIME_MULTIPLIERS: dict = {
        "risk_on": {
            # Bullish trend regime: momentum signals reliable, fundas lag
            "technical":   1.30,
            "liquid":      1.20,
            "social":      1.10,
            "fundamental": 0.80,
            "vision":      0.90,
            "insider":     0.90,
        },
        "risk_off": {
            # Defensive regime: fundas + risk matter most, pure momentum dangerous
            "fundamental": 1.30,
            "vision":      1.10,
            "risk":        1.20,   # risk agent score matters more
            "technical":   0.75,
            "social":      0.80,
            "liquid":      0.85,
            "insider":     1.00,
        },
        "choppy": {
            # Range-bound: mean-reversion agents (liquid/insider) more reliable
            "liquid":      1.25,
            "insider":     1.15,
            "fundamental": 1.10,
            "technical":   0.80,   # trend signals unreliable in chop
            "social":      0.85,
            "vision":      0.95,
        },
        "neutral": {
            # Default — no adjustment
        },
    }

    def _composite(
        self,
        f: AgentEvaluation,
        v: AgentEvaluation,
        t: AgentEvaluation,
        liquid: Optional[AgentEvaluation],
        social: Optional[AgentEvaluation],
        insider: Optional[AgentEvaluation] = None,
        squeeze: Optional[AgentEvaluation] = None,
        macro: Optional[AgentEvaluation] = None,
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
        if insider is not None:
            agents.append(("insider", insider))
        if squeeze is not None:
            agents.append(("squeeze", squeeze))
        if macro is not None:
            agents.append(("macro", macro))

        # Look up regime multipliers (default = no adjustment)
        regime_val = self._regime.regime.value if self._regime is not None else "neutral"
        multipliers = self._REGIME_MULTIPLIERS.get(regime_val, {})

        num = den = 0.0
        for key, ev in agents:
            if ev is None:
                continue
            base_w = self._weights.get(key, 0.0)
            regime_mult = multipliers.get(key, 1.0)
            w = base_w * regime_mult * max(ev.confidence, 0.05)
            num += ev.score * w
            den += w

        result = round(num / den, 2) if den else 50.0

        # Social + Squeeze convergence bonus: both agree → ±3 point boost
        if social is not None and squeeze is not None:
            if social.score >= 60 and squeeze.score >= 60:
                result = min(99.0, result + 3.0)
            elif social.score <= 40 and squeeze.score <= 40:
                result = max(1.0, result - 3.0)

        return result

    def _effective_thresholds(self, backtest_mode: bool) -> tuple[float, float]:
        """Live LONG/SHORT entry thresholds.

        In LIVE trading, strategy_weights.json (written by the optimizer's "Apply
        Optimal Params" action) overrides the configured (env) thresholds so tuned
        params take effect WITHOUT a redeploy. In backtests the configured values
        are used verbatim, so the optimizer's per-combo thresholds are never masked
        by the live file.
        """
        long_t  = self._thresholds.long_above
        short_t = self._thresholds.short_below
        if backtest_mode:
            return long_t, short_t
        try:
            w = json.loads(_WEIGHTS_FILE.read_text())
            # Only honor the file when tuning is deliberately active (see RiskAgent).
            if w.get("live_tuning_active"):
                long_t  = float(w.get("long_threshold",  long_t))
                short_t = float(w.get("short_threshold", short_t))
        except Exception:
            pass
        return long_t, short_t

    def _direction(
        self,
        composite: float,
        *,
        retail_surcharge: float = 0.0,
        long_base: Optional[float] = None,
        short_base: Optional[float] = None,
    ) -> Decision:
        """Map composite score to direction, applying regime threshold shifts.

        Research #4 (Gao et al.): retail-attention-driven momentum gets an extra
        +surcharge on the entry threshold. These setups are exploitable intraday
        (we are already day-trade-only) but require higher conviction to enter.
        """
        base_long  = long_base  if long_base  is not None else self._thresholds.long_above
        base_short = short_base if short_base is not None else self._thresholds.short_below
        long_thr  = base_long  + retail_surcharge
        short_thr = base_short - retail_surcharge
        if self._regime is not None:
            long_thr  += self._regime.long_delta
            short_thr += self._regime.short_delta
        if composite >= long_thr:
            return Decision.LONG
        if composite <= short_thr:
            return Decision.SHORT
        return Decision.PASS

    @staticmethod
    def summarise(evaluations: "Sequence[AgentEvaluation]") -> str:
        return " | ".join(
            f"{e.role.value}:{e.score}({e.confidence:.2f})" for e in evaluations
        )
