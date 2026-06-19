"""DecisionAgent — LLM meta-agent that synthesises all specialist reports
into a single LONG / SHORT / PASS decision.

This agent is NOT a BaseAgent subclass: it doesn't evaluate tickers directly;
it reads the AgentEvaluations produced by all specialist agents and asks an
LLM to make the final call.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from core.enums import AgentRole, Decision
from core.llm_adapter import LLMAdapter, parse_llm_json
from core.models import AgentEvaluation, AnalysisContext
from core.trade_memory import TradeMemory

logger = logging.getLogger(__name__)

_WEIGHTS_FILE = Path(__file__).parent.parent / "data" / "strategy_weights.json"

_DIRECTIONAL_ROLES = {
    AgentRole.FUNDAMENTAL,
    AgentRole.VISION,
    AgentRole.TECHNICAL,
    AgentRole.LIQUID,
    AgentRole.INSIDER,
    AgentRole.SQUEEZE,
}

_SYSTEM_PROMPT = (
    "You are the Chief Decision Officer for an algorithmic day-trading bot. "
    "You receive reports from specialist agents and output a single trading decision. "
    "Respond ONLY with valid JSON — no explanation, no markdown, just JSON."
)

_DEBATE_SYSTEM_PROMPT = (
    "You chair the trading committee for an algorithmic day-trading bot. "
    "You hear a bull case and a bear case, then rule impartially as judge. "
    "Favour the side with more independent agent support and fewer unresolved "
    "concerns; do not anchor on either advocate. "
    "Respond ONLY with valid JSON — no explanation, no markdown, just JSON."
)


def _sentiment_label(score: float) -> str:
    if score >= 60:
        return "BULLISH"
    if score <= 40:
        return "BEARISH"
    return "NEUTRAL"


def _format_agent_block(ev: AgentEvaluation) -> str:
    label = _sentiment_label(ev.score)
    lines = [
        f"[{ev.role.value.upper()}]  {label}  score={ev.score:.0f}/100  "
        f"confidence={ev.confidence * 100:.0f}%",
        f"  Rationale: {ev.rationale}",
    ]
    r = ev.reasoning or {}

    if ev.role is AgentRole.TECHNICAL and "signals" in r:
        signals = r["signals"][:4]
        if signals:
            lines.append("  Top signals:")
            for s in signals:
                lines.append(
                    f"    • {s.get('name','')} [{s.get('direction','')}]"
                    f"  {s.get('display','')}  score={s.get('score','')}"
                )

    elif ev.role is AgentRole.FUNDAMENTAL and "headlines_sample" in r:
        headlines = r["headlines_sample"][:2]
        if headlines:
            lines.append("  Headlines:")
            for h in headlines:
                lines.append(f"    • {h}")

    elif ev.role is AgentRole.VISION and "pattern_identified" in r:
        lines.append(f"  Pattern: {r['pattern_identified']}")

    elif ev.role is AgentRole.LIQUID and "relative_volume" in r:
        lines.append(f"  Relative volume: {r['relative_volume']}")

    elif ev.role is AgentRole.INSIDER:
        if "trade_count" in r:
            lines.append(f"  Congressional trades (recent): {r.get('trade_count', 0)}")
        elif ev.rationale:
            lines.append(f"  {ev.rationale}")

    elif ev.role is AgentRole.SQUEEZE:
        if "short_ratio_pct" in r:
            lines.append(
                f"  Short ratio: {r.get('short_ratio_pct', 'n/a')}  "
                f"Setup: {r.get('setup', 'n/a')}  "
                f"Rel-vol: {r.get('relative_volume', 'n/a')}x"
            )

    return "\n".join(lines)


def _format_risk_block(risk_ev: Optional[AgentEvaluation]) -> str:
    if risk_ev is None:
        return "[RISK]  n/a"
    veto_str = "VETO ACTIVE" if risk_ev.veto else "no veto"
    return (
        f"[RISK]  {veto_str}  score={risk_ev.score:.0f}/100\n"
        f"  Rationale: {risk_ev.rationale}"
    )


def _load_perf_block() -> str:
    try:
        with open(_WEIGHTS_FILE, encoding="utf-8") as f:
            weights = json.load(f)
        win_rate   = weights.get("win_rate_30d")
        long_wr    = weights.get("long_win_rate")
        short_wr   = weights.get("short_win_rate")
        bias       = weights.get("bias", "neutral")
        atr_stop   = weights.get("atr_stop_multiple", 2.0)
        atr_target = weights.get("atr_target_multiple", 3.0)

        if win_rate is None:
            return "PERFORMANCE CONTEXT (apply these directives):\n- No trade history yet — evaluate each signal on its merits."

        long_wr_str  = f"{long_wr:.1f}"  if long_wr  is not None else "n/a"
        short_wr_str = f"{short_wr:.1f}" if short_wr is not None else "n/a"
        if bias == "long":
            directive = "Prefer LONG signals unless strong short evidence overrides."
        elif bias == "short":
            directive = "Prefer SHORT signals unless strong long evidence overrides."
        else:
            directive = "No directional bias — evaluate each signal on its merits."
        return (
            "PERFORMANCE CONTEXT (apply these directives):\n"
            f"- Historical bias: {bias} (long win rate: {long_wr_str}%, short win rate: {short_wr_str}%)\n"
            f"- Current win rate (last 30 trades): {win_rate:.1f}%\n"
            f"- DIRECTIVE: {directive}\n"
            f"- ATR stop multiple in use: {atr_stop}× | ATR target multiple: {atr_target}×"
        )
    except Exception:
        return ""


class DecisionAgent:
    """LLM meta-agent: synthesises all specialist agent outputs into one decision."""

    def __init__(
        self,
        *,
        anthropic_api_key: str = "",
        gemini_api_key: str = "",
        model: str = "",
    ) -> None:
        self._llm = LLMAdapter(
            gemini_key=gemini_api_key,
            anthropic_key=anthropic_api_key,
            anthropic_model=model,
        )
        # Bull/bear deliberation prompt (TradingAgents-style). One LLM call: the
        # model argues both sides before ruling, which improves calibration
        # without the cost of multiple round-trips. Toggle with DECISION_DEBATE.
        self._debate = os.environ.get("DECISION_DEBATE", "true").lower() in ("1", "true", "yes")
        # Reflection memory: read-only here (the PortfolioManager records the
        # actual opened trades and their outcomes); we inject recent lessons.
        self._memory = TradeMemory()

    @property
    def available(self) -> bool:
        return self._llm.has_llm

    async def decide(
        self,
        ctx: AnalysisContext,
        evaluations: list[AgentEvaluation],
        regime_value: str,
        regime_rationale: str,
    ) -> Tuple[Decision, float, dict]:
        """Synthesise all agent evaluations into a single trading decision.

        Returns:
            (Decision, composite_score 1-100, reasoning dict)
        Falls back to (Decision.PASS, 50.0, {"error": ...}) on any failure.
        """
        try:
            ticker = ctx.ticker
            price = ctx.last_price or 0.0

            directional_evals = [
                ev for ev in evaluations if ev.role in _DIRECTIONAL_ROLES
            ]
            risk_eval = next(
                (ev for ev in evaluations if ev.role is AgentRole.RISK), None
            )

            agent_block = "\n\n".join(
                _format_agent_block(ev) for ev in directional_evals
            ) or "(no directional agents available)"

            risk_block = _format_risk_block(risk_eval)
            perf_block = _load_perf_block()
            lessons_block = self._memory.recent_lessons()

            # Two independent positioning signals (congressional flow + short
            # squeeze setup) agreeing → elevated conviction.
            insider_ev = next((ev for ev in evaluations if ev.role is AgentRole.INSIDER), None)
            squeeze_ev = next((ev for ev in evaluations if ev.role is AgentRole.SQUEEZE), None)
            convergence_note = ""
            if insider_ev is not None and squeeze_ev is not None:
                if insider_ev.score >= 60 and squeeze_ev.score >= 60:
                    convergence_note = "\n- SIGNAL CONVERGENCE: Insider AND Squeeze both bullish — elevated conviction."
                elif insider_ev.score <= 40 and squeeze_ev.score <= 40:
                    convergence_note = "\n- SIGNAL CONVERGENCE: Insider AND Squeeze both bearish — elevated conviction."

            context_block = (
                f"Stock: {ticker}  Price: ${price:.2f}\n"
                f"Regime: {regime_value.upper()} — {regime_rationale}\n"
                "\nAGENT REPORTS:\n\n"
                f"{agent_block}\n\n"
                f"{risk_block}\n"
                + (f"\n{perf_block}\n" if perf_block else "")
                + (f"\n{lessons_block}\n" if lessons_block else "")
            )

            if self._debate:
                user_prompt = (
                    context_block
                    + "\nDELIBERATE AS A PANEL, THEN RULE AS JUDGE:\n"
                    "1. BULL CASE: the strongest argument to go LONG, citing specific agents above.\n"
                    "2. BEAR CASE: the strongest argument to go SHORT or stay out, citing specific agents.\n"
                    "3. JUDGE: weigh both impartially; side with broader independent agent support.\n"
                    "RULES:\n"
                    "- If Risk veto is active, decision MUST be PASS.\n"
                    "- In RISK_OFF regime, only go LONG on very high conviction (multiple agents bullish).\n"
                    "- INSIDER and SQUEEZE are directional signals — include them.\n"
                    "- Gap fade signals (small gaps <0.5%) have 88% intraday fill rate — weight accordingly.\n"
                    f"- composite_score: 1-100 (>60=bullish, <40=bearish, 40-60=neutral){convergence_note}\n"
                    "\nOutput JSON only:\n"
                    '{"bull_case":"<max 30 words>","bear_case":"<max 30 words>",'
                    '"decision":"LONG|SHORT|PASS","composite_score":<int>,'
                    '"rationale":"<max 30 words>","key_factors":["...","..."],'
                    '"concerns":["..."]}'
                )
                system_prompt = _DEBATE_SYSTEM_PROMPT
            else:
                user_prompt = (
                    context_block
                    + "\nINSTRUCTIONS:\n"
                    "- If Risk veto is active, you MUST output decision=PASS.\n"
                    "- Consider where agents agree and where they conflict.\n"
                    "- In RISK_OFF regime, only enter LONG with very high conviction "
                    "(multiple agents bullish).\n"
                    "- INSIDER and SQUEEZE are directional signals — include them in your analysis.\n"
                    "- Gap fade signals (small gaps <0.5%) have 88% intraday fill rate — weight accordingly.\n"
                    f"- composite_score: 1-100 (>60=bullish, <40=bearish, 40-60=neutral){convergence_note}\n"
                    "\nOutput JSON only:\n"
                    '{"decision":"LONG|SHORT|PASS","composite_score":<int>,'
                    '"rationale":"<max 30 words>","key_factors":["...","..."],'
                    '"concerns":["..."]}'
                )
                system_prompt = _SYSTEM_PROMPT

            raw = await self._llm.chat(user_prompt, system=system_prompt)
            if not raw:
                raise ValueError("LLM returned empty response")

            parsed = parse_llm_json(raw)
            if parsed is None:
                # Fallback: try plain json.loads on the raw string
                try:
                    parsed = json.loads(raw.strip())
                except json.JSONDecodeError:
                    raise ValueError(f"Cannot parse LLM JSON: {raw[:200]}")

            decision_str = str(parsed.get("decision", "PASS")).upper()
            try:
                decision = Decision(decision_str)
            except ValueError:
                decision = Decision.PASS

            composite = float(parsed.get("composite_score", 50))
            composite = max(1.0, min(100.0, composite))

            logger.info(
                "DecisionAgent %s → %s (composite=%.1f)  rationale: %s",
                ticker,
                decision.value,
                composite,
                parsed.get("rationale", ""),
            )

            return decision, composite, parsed

        except Exception as exc:
            logger.warning("DecisionAgent failed for %s: %s", ctx.ticker, exc)
            return Decision.PASS, 50.0, {"error": str(exc)}
