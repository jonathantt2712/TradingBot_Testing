"""DecisionAgent — LLM meta-agent that synthesises all specialist reports
into a single LONG / SHORT / PASS decision.

This agent is NOT a BaseAgent subclass: it doesn't evaluate tickers directly;
it reads the AgentEvaluations produced by all specialist agents and asks an
LLM to make the final call.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Tuple

from core.enums import AgentRole, Decision
from core.llm_adapter import LLMAdapter, parse_llm_json
from core.models import AgentEvaluation, AnalysisContext

logger = logging.getLogger(__name__)

_WEIGHTS_FILE = Path(__file__).parent.parent / "data" / "strategy_weights.json"

_DIRECTIONAL_ROLES = {
    AgentRole.FUNDAMENTAL,
    AgentRole.VISION,
    AgentRole.TECHNICAL,
    AgentRole.LIQUID,
    AgentRole.SOCIAL,
}

_SYSTEM_PROMPT = (
    "You are the Chief Decision Officer for an algorithmic day-trading bot. "
    "You receive reports from specialist agents and output a single trading decision. "
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

    elif ev.role is AgentRole.SOCIAL:
        if "bull_weight" in r or "bear_weight" in r:
            lines.append(
                f"  Bull weight: {r.get('bull_weight', 'n/a')}  "
                f"Bear weight: {r.get('bear_weight', 'n/a')}"
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
        win_rate = weights.get("win_rate_30d")
        if win_rate is None:
            return ""
        lines = [f"BACKTEST PERFORMANCE (30d win rate: {win_rate:.1%})"]
        for key, val in weights.items():
            if key != "win_rate_30d":
                lines.append(f"  {key}: {val}")
        return "\n".join(lines)
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

            user_prompt = (
                f"Stock: {ticker}  Price: ${price:.2f}\n"
                f"Regime: {regime_value.upper()} — {regime_rationale}\n"
                "\nAGENT REPORTS:\n\n"
                f"{agent_block}\n\n"
                f"{risk_block}\n"
                + (f"\n{perf_block}\n" if perf_block else "")
                + "\nINSTRUCTIONS:\n"
                "- If Risk veto is active, you MUST output decision=PASS.\n"
                "- Consider where agents agree and where they conflict.\n"
                "- In RISK_OFF regime, only enter LONG with very high conviction "
                "(multiple agents bullish).\n"
                "- composite_score: 1-100 (>60=bullish, <40=bearish, 40-60=neutral)\n"
                "\nOutput JSON only:\n"
                '{"decision":"LONG|SHORT|PASS","composite_score":<int>,'
                '"rationale":"<max 30 words>","key_factors":["...","..."],'
                '"concerns":["..."]}'
            )

            raw = await self._llm.chat(user_prompt, system=_SYSTEM_PROMPT)
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
