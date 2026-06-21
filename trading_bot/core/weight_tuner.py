"""Online weight tuner — adapts agent weights from resolved trade outcomes.

Every time a position closes, the tuner re-analyses the last WINDOW resolved
trades, measures each agent's directional accuracy, and writes updated weights
and entry thresholds to data/strategy_weights.json.  The PortfolioManager
reads that file (TTL-cached) so the composite score and gates adapt without
a restart — this is the core "learn on the run" loop.

Accuracy model
--------------
For each resolved trade we know:
  - which direction the bot took (LONG / SHORT)
  - whether it won (pnl > 0)
  - what each agent scored at decision time

An agent is "correct" on a trade if it pointed the same direction as the
profitable outcome.  Accuracy above 50% (better than random) earns a weight
multiplier > 1; below 50% earns a penalty; always-wrong is floored at 0.1×.
Multipliers are applied to the configured base weights and renormalised to 1.

Threshold adaptation
--------------------
Overall win rate drives entry-threshold nudges:
  < 40% win rate → tighten (raise LONG bar, lower SHORT bar) by NUDGE points
  > 65% win rate → loosen (lower LONG bar, raise SHORT bar) by NUDGE points
  Thresholds are clamped: LONG in [55, 75], SHORT in [25, 45].
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.trade_memory import TradeMemory

logger = logging.getLogger(__name__)

_WEIGHTS_FILE = Path(__file__).parent.parent / "data" / "strategy_weights.json"
_MIN_TRADES = 10    # don't tune until we have this many resolved outcomes
_WINDOW = 30        # rolling window size
_SKILL_SCALE = 2.0  # accuracy-to-multiplier scale: perfect → 2×, random → 1×, always-wrong → 0.1×
_MIN_MULT = 0.1     # floor so no agent is fully suppressed
_NUDGE = 1.0        # threshold shift per cycle (points)


class WeightTuner:
    """Adapts agent weights and entry thresholds from live trade outcomes."""

    def __init__(self, base_weights: dict[str, float]) -> None:
        # Normalised base weights from settings (summing to ~1.0).
        # Multipliers are applied relative to these so tuning is always
        # anchored to the operator's configured starting point.
        self._base = dict(base_weights)

    def update(self, memory: "TradeMemory") -> None:
        """Re-analyse resolved trades and persist updated weights. Silent on error."""
        try:
            self._run(memory._load())
        except Exception:
            logger.debug("WeightTuner.update failed", exc_info=True)

    def _run(self, entries: list[dict]) -> None:
        resolved = [
            e for e in entries
            if e.get("outcome_pnl") is not None
            and isinstance(e.get("agent_scores"), dict)
            and e.get("agent_scores")
            and e.get("decision") in ("LONG", "SHORT")
        ]
        if len(resolved) < _MIN_TRADES:
            return

        window = resolved[-_WINDOW:]

        agent_correct: dict[str, int] = {}
        agent_seen: dict[str, int] = {}
        wins = long_wins = long_n = short_wins = short_n = 0

        for e in window:
            pnl = float(e["outcome_pnl"])
            direction: str = e["decision"]
            won = pnl > 0
            wins += won
            if direction == "LONG":
                long_n += 1
                long_wins += won
            else:
                short_n += 1
                short_wins += won

            for agent, score in e["agent_scores"].items():
                agreed = (float(score) >= 50) if direction == "LONG" else (float(score) <= 50)
                correct = agreed == won
                agent_correct[agent] = agent_correct.get(agent, 0) + correct
                agent_seen[agent] = agent_seen.get(agent, 0) + 1

        n = len(window)
        win_rate = wins / n * 100.0
        long_wr = long_wins / long_n * 100.0 if long_n else None
        short_wr = short_wins / short_n * 100.0 if short_n else None

        # Per-agent weight multiplier based on accuracy above chance
        mults: dict[str, float] = {}
        for agent, seen in agent_seen.items():
            if seen < 5:
                mults[agent] = 1.0
                continue
            accuracy = agent_correct[agent] / seen
            skill = accuracy - 0.5          # -0.5 … +0.5
            mults[agent] = round(max(_MIN_MULT, 1.0 + skill * _SKILL_SCALE), 3)

        # Apply multipliers to base weights and renormalise to sum to 1
        raw = {k: v * mults.get(k, 1.0) for k, v in self._base.items()}
        total = sum(raw.values()) or 1.0
        tuned = {k: round(v / total, 4) for k, v in raw.items()}

        # Load existing file to preserve any manually set fields
        try:
            existing = json.loads(_WEIGHTS_FILE.read_text()) if _WEIGHTS_FILE.exists() else {}
        except Exception:
            existing = {}

        long_thr = float(existing.get("long_threshold", 60.0))
        short_thr = float(existing.get("short_threshold", 40.0))

        if win_rate < 40.0:
            long_thr = min(75.0, long_thr + _NUDGE)
            short_thr = max(25.0, short_thr - _NUDGE)
            logger.info("WeightTuner: low win rate %.1f%% → tightening to L=%.1f S=%.1f",
                        win_rate, long_thr, short_thr)
        elif win_rate > 65.0:
            long_thr = max(55.0, long_thr - _NUDGE)
            short_thr = min(45.0, short_thr + _NUDGE)
            logger.info("WeightTuner: high win rate %.1f%% → loosening to L=%.1f S=%.1f",
                        win_rate, long_thr, short_thr)

        if long_wr is not None and short_wr is not None:
            bias = "long" if long_wr > short_wr + 10 else ("short" if short_wr > long_wr + 10 else "neutral")
        else:
            bias = existing.get("bias", "neutral")

        out = {
            **existing,
            "live_tuning_active": True,
            "agent_weights": tuned,
            "agent_multipliers": mults,
            "win_rate_30d": round(win_rate, 1),
            "long_win_rate": round(long_wr, 1) if long_wr is not None else None,
            "short_win_rate": round(short_wr, 1) if short_wr is not None else None,
            "bias": bias,
            "long_threshold": round(long_thr, 1),
            "short_threshold": round(short_thr, 1),
            "sample_size": n,
        }
        _WEIGHTS_FILE.parent.mkdir(exist_ok=True)
        _WEIGHTS_FILE.write_text(json.dumps(out, indent=2), encoding="utf-8")
        logger.info(
            "WeightTuner: %d trades win=%.1f%% bias=%s L=%.1f S=%.1f mults=%s",
            n, win_rate, bias, long_thr, short_thr,
            {k: f"{v:.2f}x" for k, v in mults.items()},
        )
