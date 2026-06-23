"""Per-agent scorecard — each agent's individual directional track record.

The WeightTuner already turns these accuracies into the weight multipliers it
applies to the composite blend; this module surfaces the SAME signal *per agent*
(hit rate, sample size, and the live weight/multiplier in force) so it can be
inspected — which agents are actually earning their keep. The correctness rule
mirrors WeightTuner exactly so the numbers line up.

Pure and fail-soft: callers pass in already-loaded data; nothing here raises.
"""
from __future__ import annotations

from typing import Optional

from core.weight_tuner import _WINDOW


def _resolved(closed_trades: list[dict]) -> list[dict]:
    """Trades usable for scoring: realised P&L, a real direction, and evaluations."""
    out: list[dict] = []
    for t in closed_trades:
        if t.get("pnl") is None:
            continue
        if str(t.get("direction", "")).upper() not in ("LONG", "SHORT"):
            continue
        if not (t.get("evaluations") or []):
            continue
        out.append(t)
    return out


def compute_agent_scorecards(
    closed_trades: list[dict],
    weights: Optional[dict] = None,
    window: int = _WINDOW,
) -> list[dict]:
    """Per-agent hit rate / sample size over the last ``window`` resolved trades.

    ``weights`` is the parsed strategy_weights.json (for the live weight and
    multiplier currently in force). An agent is "correct" on a trade when the
    direction it leaned (score >= 50 for LONG, <= 50 for SHORT) matches the
    winning outcome — identical to WeightTuner's rule.
    """
    weights = weights if isinstance(weights, dict) else {}
    agent_weights = weights.get("agent_weights") or {}
    agent_mults = weights.get("agent_multipliers") or {}

    resolved = _resolved(closed_trades)[-window:]

    seen: dict[str, int] = {}
    correct: dict[str, int] = {}
    agreed_n: dict[str, int] = {}
    pnl_when_agreed: dict[str, float] = {}

    for t in resolved:
        try:
            pnl = float(t["pnl"])
        except (TypeError, ValueError):
            continue  # malformed record — honour the "nothing here raises" contract
        won = pnl > 0
        direction = str(t["direction"]).upper()
        for ev in (t.get("evaluations") or []):
            if not isinstance(ev, dict):
                continue
            role = ev.get("role")
            score = ev.get("score")
            if not role or score is None:
                continue
            try:
                score_f = float(score)
            except (TypeError, ValueError):
                continue
            role = str(role)
            agreed = (score_f >= 50) if direction == "LONG" else (score_f <= 50)
            seen[role] = seen.get(role, 0) + 1
            correct[role] = correct.get(role, 0) + (agreed == won)
            if agreed:
                agreed_n[role] = agreed_n.get(role, 0) + 1
                pnl_when_agreed[role] = pnl_when_agreed.get(role, 0.0) + pnl

    cards: list[dict] = []
    for role in sorted(seen):
        n = seen[role]
        an = agreed_n.get(role, 0)
        cards.append({
            "agent": role,
            "samples": n,
            "hit_rate": round(100 * correct[role] / n, 1) if n else None,
            "weight": agent_weights.get(role),
            "multiplier": agent_mults.get(role),
            "avg_pnl_when_agreed": round(pnl_when_agreed[role] / an, 2) if an else None,
        })
    return cards
