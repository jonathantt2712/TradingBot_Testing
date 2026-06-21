"""Portfolio Manager -- orchestrator / execution brain.

Pipeline per ticker:
1. Run Fundamental, Vision, Technical, Liquid (opt), Risk CONCURRENTLY.
2. Blend directional scores by configured weights -> composite [1, 100].
3. Map composite to LONG / SHORT / PASS via thresholds.
4. Apply Risk veto and minimum-risk-score gate.
5. Build concrete plan, size the order, route bracket through the broker.
"""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from config.settings import DecisionThresholds, Settings
from core.enums import AgentRole, Decision, OrderSide
from core.models import AgentEvaluation, AnalysisContext, TradeDecision
from core.trade_memory import TradeMemory
from agents.fundamental_agent import FundamentalAgent
from agents.liquid_agent import LiquidAgent
from agents.regime_agent import MarketRegime, RegimeSnapshot
from agents.risk_agent import RiskAgent
from agents.insider_agent import InsiderAgent
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

# Coarse correlation groups for the concentration cap. A symbol may belong to
# several groups; the cap trips if ANY of its groups is already at the limit.
# Symbols not listed here are treated as uncorrelated (never capped). This is a
# deliberately simple heuristic — the goal is only to stop the bot stacking,
# say, five mega-cap tech names that move together into one undiversified bet.
_CORRELATION_GROUPS: dict[str, set[str]] = {
    "mega_tech":  {"AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA", "AMD", "NFLX", "AVGO"},
    "semis":      {"NVDA", "AMD", "AVGO", "INTC", "MU", "TSM", "QCOM", "ASML", "ARM", "SMCI"},
    "index_etf":  {"SPY", "QQQ", "IWM", "DIA", "VOO", "VTI"},
    "ev":         {"TSLA", "RIVN", "LCID", "NIO"},
    "crypto_eq":  {"COIN", "MARA", "RIOT", "MSTR", "CLSK"},
}


def _correlation_groups(symbol: str) -> set[str]:
    """Return the set of correlation groups a symbol belongs to (may be empty)."""
    sym = symbol.upper()
    return {g for g, members in _CORRELATION_GROUPS.items() if sym in members}


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
        insider: Optional["InsiderAgent"] = None,
        squeeze: Optional["SqueezeAgent"] = None,
        macro: Optional["MacroSignalAgent"] = None,
        decision_agent: Optional[DecisionAgent] = None,
    ) -> None:
        self.settings = settings
        self.broker = broker
        self.fundamental = fundamental
        self.vision = vision
        self.technical = technical
        self.risk = risk
        self.liquid = liquid
        self.insider = insider
        self.squeeze = squeeze
        self.macro   = macro
        self._decision_agent = decision_agent
        self._weights = settings.weights.as_map()
        self._thresholds: DecisionThresholds = settings.thresholds
        self._regime: Optional[RegimeSnapshot] = None

        # Daily-loss kill switch state (reset each ET trading day)
        self._day_start_equity: Optional[float] = None
        self._kill_switch_date: Optional[date] = None
        self._halted: bool = False
        self._intraday_peak_equity: Optional[float] = None

        # Trade-protection state (freqtrade-style circuit breakers). Driven by
        # _observe_positions(), which diffs the open-position set across cycles.
        self._open_upl: dict[str, float] = {}             # symbol → last-seen unrealized P&L
        self._cooldown_until: dict[str, datetime] = {}    # symbol → re-entry allowed after (ET)
        self._recent_stops: list[datetime] = []           # losing-exit timestamps (ET)
        self._streak_halt_until: Optional[datetime] = None

        # Reflection memory: records outcomes when positions close so the
        # DecisionAgent can learn from the bot's own track record.
        self._memory = TradeMemory()

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
        vision_idx = liquid_idx = squeeze_idx = None
        if self.vision is not None:
            vision_idx = len(coros)
            coros.append(self.vision.safe_evaluate(ctx))
        if self.liquid is not None:
            liquid_idx = len(coros)
            coros.append(self.liquid.safe_evaluate(ctx))
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
            # LLM unavailable (Gemini quota/outage) → fall back to weighted composite
            # so recommendations still flow rather than everything becoming PASS.
            if decision_meta and "error" in decision_meta:
                composite = self._composite(fundamental, vision_eval, technical, liquid_eval, insider_eval, squeeze_eval, macro_eval)
                retail_surcharge = float(technical.data.get("retail_surcharge", 0.0)) if technical and technical.data else 0.0
                long_base, short_base = self._effective_thresholds(getattr(ctx, "backtest_mode", False))
                decision = self._direction(composite, retail_surcharge=retail_surcharge,
                                           long_base=long_base, short_base=short_base)
                decision_meta["fallback"] = "LLM unavailable — weighted composite used"
                logger.debug("%s: DecisionAgent error → composite fallback (%.1f)", ctx.ticker, composite)
        else:
            composite = self._composite(fundamental, vision_eval, technical, liquid_eval, insider_eval, squeeze_eval, macro_eval)
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

        # Conviction scaling: boost size up to +20% when composite is far from threshold.
        # Score ≥85 (LONG) or ≤15 (SHORT) → +20%.  Near-threshold → 0% boost.
        # Respects max_position_pct cap to avoid over-sizing on any single trade.
        if plan is not None and plan.qty > 0:
            if decision is Decision.LONG:
                conv = min(0.20, max(0.0, (composite - 65.0) / 100.0))
            else:  # SHORT
                conv = min(0.20, max(0.0, (35.0 - composite) / 100.0))
            if conv > 0:
                equity = float(ctx.account.get("equity", 0.0) or 0.0)
                max_qty = (equity * self.settings.risk.max_position_pct / plan.entry) if plan.entry > 0 else plan.qty
                new_qty = float(int(min(plan.qty * (1.0 + conv), max_qty)))
                if new_qty > plan.qty:
                    logger.info(
                        "%s conviction boost: composite=%.1f → +%.0f%% size (%g→%g shares)",
                        ctx.ticker, composite, conv * 100, plan.qty, new_qty,
                    )
                    plan.qty = new_qty

        # Disagreement haircut: when the directional agents strongly disagree, the
        # composite is a blend of conflicting views — low conviction — so risk
        # less on the trade. Dispersion is the population std of the agent scores
        # on the 1..100 scale (RISK excluded; it is a gate, not a direction).
        if plan is not None and plan.qty > 0:
            dispersion = self._directional_dispersion(evaluations)
            if dispersion >= 25.0:
                plan.qty = float(int(plan.qty * 0.5))
                logger.info("%s agent disagreement std=%.1f >= 25: size 50%%", ctx.ticker, dispersion)
            elif dispersion >= 18.0:
                plan.qty = float(int(plan.qty * 0.75))
                logger.info("%s agent disagreement std=%.1f >= 18: size 75%%", ctx.ticker, dispersion)

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
            self._intraday_peak_equity = equity
            self._halted = False
            return False

        # From-open daily loss limit
        limit = self._day_start_equity * (1.0 - self.settings.risk.max_daily_loss_pct)
        if not self._halted and equity < limit:
            self._halted = True
            logger.error(
                "KILL SWITCH: equity %.2f below daily loss limit %.2f "
                "(start %.2f, max loss %.1f%%) — no new entries today",
                equity, limit, self._day_start_equity,
                self.settings.risk.max_daily_loss_pct * 100,
            )

        # Intraday peak-to-trough drawdown halt (freqtrade MaxDrawdown, equity
        # mode): catches give-backs the from-open stop misses (e.g. up 4% then
        # back to +1%). Tracks the running intraday peak.
        if self._intraday_peak_equity is None or equity > self._intraday_peak_equity:
            self._intraday_peak_equity = equity
        dd_pct = self.settings.risk.intraday_drawdown_halt_pct
        if not self._halted and dd_pct > 0 and self._intraday_peak_equity > 0:
            peak_limit = self._intraday_peak_equity * (1.0 - dd_pct)
            if equity < peak_limit:
                self._halted = True
                logger.error(
                    "DRAWDOWN HALT: equity %.2f is >%.1f%% below intraday peak %.2f "
                    "— no new entries today",
                    equity, dd_pct * 100, self._intraday_peak_equity,
                )
        return self._halted

    def _observe_positions(self, positions: Sequence[dict]) -> None:
        """Diff the open-position set across cycles to detect exits.

        Drives three things from a single observation:
          • CooldownPeriod — a symbol that just closed is put on a re-entry
            cooldown to avoid whipsaw churn.
          • StoplossGuard — exits whose last-seen unrealized P&L was negative
            count toward a loss streak; ``loss_streak_limit`` losses inside the
            rolling window halt all new entries for ``loss_streak_halt_min``.
          • Reflection memory — the exit's last-seen P&L is recorded against the
            decision that opened it, so the DecisionAgent can learn from it.

        Approximation: realised P&L is taken as the last unrealized P&L seen
        while the position was open. Broker-agnostic and accurate enough for a
        circuit breaker and an advisory memory.
        """
        now = datetime.now(_ET)
        cfg = self.settings.risk
        current: dict[str, float] = {}
        for p in positions:
            sym = str(p.get("symbol", "")).upper()
            if not sym:
                continue
            try:
                current[sym] = float(p.get("unrealized_pl", 0.0) or 0.0)
            except (TypeError, ValueError):
                current[sym] = 0.0

        # Symbols open last cycle but gone now → they exited.
        for sym, last_upl in self._open_upl.items():
            if sym in current:
                continue
            if cfg.reentry_cooldown_min > 0:
                self._cooldown_until[sym] = now + timedelta(minutes=cfg.reentry_cooldown_min)
            if last_upl < 0:
                self._recent_stops.append(now)
                logger.info("%s exited at a loss (≈%.2f) — loss-streak count %d",
                            sym, last_upl, len(self._recent_stops))
            try:
                self._memory.record_outcome(sym, last_upl)
            except Exception:
                logger.debug("memory record_outcome failed", exc_info=True)

        self._open_upl = current

        # Prune the streak window and trip the guard if breached.
        cutoff = now - timedelta(minutes=cfg.loss_streak_window_min)
        self._recent_stops = [t for t in self._recent_stops if t >= cutoff]
        if cfg.loss_streak_limit > 0 and len(self._recent_stops) >= cfg.loss_streak_limit:
            self._streak_halt_until = now + timedelta(minutes=cfg.loss_streak_halt_min)
            logger.error(
                "STOPLOSS GUARD: %d losing exits within %dmin — pausing new entries for %dmin",
                len(self._recent_stops), cfg.loss_streak_window_min, cfg.loss_streak_halt_min,
            )
            self._recent_stops.clear()

    async def refresh_protections(self) -> None:
        """Fetch positions and update protection state (once per scan cycle).

        On a broker error the update is skipped — protections never block on
        stale state, they only act on confirmed exits.
        """
        if self.broker is None:
            return
        try:
            positions = await self.broker.get_positions()
        except Exception:
            logger.debug("refresh_protections: positions unavailable", exc_info=True)
            return
        self._observe_positions(positions)

    async def _entry_allowed(self, ticker: str) -> bool:
        """Pre-trade gate: protections, no duplicate exposure, max open positions.

        Fails closed — if portfolio state cannot be fetched, the entry is
        skipped rather than risked blind.
        """
        symbol = ticker.upper()
        now = datetime.now(_ET)

        # Re-entry cooldown (freqtrade CooldownPeriod)
        cd_until = self._cooldown_until.get(symbol)
        if cd_until is not None and now < cd_until:
            logger.info("%s entry skipped: re-entry cooldown until %s ET",
                        ticker, cd_until.strftime("%H:%M"))
            return False

        # Loss-streak halt (freqtrade StoplossGuard)
        if self._streak_halt_until is not None and now < self._streak_halt_until:
            logger.info("%s entry skipped: stoploss-guard halt until %s ET",
                        ticker, self._streak_halt_until.strftime("%H:%M"))
            return False

        try:
            positions = await self.broker.get_positions()
            open_orders = await self.broker.get_open_orders()
        except Exception as exc:
            logger.error("%s entry blocked: cannot verify portfolio state (%s)", ticker, exc)
            return False

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

        # Concentration cap: limit simultaneous positions in one correlation group.
        cand_groups = _correlation_groups(symbol)
        cap = self.settings.risk.max_correlated_positions
        if cand_groups and cap > 0:
            open_syms = [str(p.get("symbol", "")).upper() for p in positions]
            for g in cand_groups:
                in_group = sum(1 for s in open_syms if g in _correlation_groups(s))
                if in_group >= cap:
                    logger.info(
                        "%s entry skipped: %d positions already in correlated group '%s' (max %d)",
                        ticker, in_group, g, cap,
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
            # Remember the opened trade so the DecisionAgent can later learn from
            # its outcome (resolved by _observe_positions when the position exits).
            try:
                meta = decision.decision_meta or {}
                self._memory.record_decision(
                    decision.ticker, decision.decision.value, decision.composite_score,
                    factors=meta.get("key_factors"), concerns=meta.get("concerns"),
                )
            except Exception:
                logger.debug("memory record_decision failed", exc_info=True)
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
            "liquid":      0.85,
            "insider":     1.00,
        },
        "choppy": {
            # Range-bound: mean-reversion agents (liquid/insider) more reliable
            "liquid":      1.25,
            "insider":     1.15,
            "fundamental": 1.10,
            "technical":   0.80,   # trend signals unreliable in chop
            "vision":      0.95,
        },
        "neutral": {
            # Default — no adjustment
        },
    }

    @staticmethod
    def _directional_dispersion(evaluations: "Sequence[AgentEvaluation]") -> float:
        """Population std of the directional agent scores (excludes RISK, a gate).

        High dispersion means the agents strongly disagree on direction, so the
        composite is a low-conviction average of conflicting views.
        """
        scores = [
            e.score for e in evaluations
            if e is not None and e.role is not AgentRole.RISK
        ]
        if len(scores) < 2:
            return 0.0
        return float(statistics.pstdev(scores))

    def _composite(
        self,
        f: AgentEvaluation,
        v: AgentEvaluation,
        t: AgentEvaluation,
        liquid: Optional[AgentEvaluation],
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

        return round(num / den, 2) if den else 50.0

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
