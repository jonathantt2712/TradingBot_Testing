"""Simulate the online-learning loop end-to-end so the Learning view has a
realistic, populated track record before enough *real* scored trades exist.

This is NOT fake numbers painted onto a chart. It generates a stream of
synthetic trades where every agent has a hidden "skill" (probability it points
the right way), then feeds those trades through the *real* ``WeightTuner`` —
the exact same engine that runs live. The weight drift, multiplier spread,
win-rate curve and threshold nudges you see are the genuine output of the
tuner reacting to the simulated track record.

Everything written here is tagged ``"simulated": true`` so the dashboard can
badge it honestly and a single real tuning step can supersede it.

Run locally:   python simulate_learning.py
On Railway:    POST /api/learning/simulate  (see api_server.py)
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

from config.settings import AgentWeights
from core import weight_tuner
from core.weight_tuner import WeightTuner, _HISTORY_FILE, _WEIGHTS_FILE

# Hidden per-agent skill: P(agent points the same way as the eventual outcome).
# 0.50 == coin-flip. The spread is deliberate so the tuner has something real to
# learn — technical/fundamental earn trust, insider/squeeze get demoted.
_AGENT_SKILL = {
    "technical":   0.64,
    "fundamental": 0.59,
    "macro":       0.55,
    "vision":      0.52,
    "liquid":      0.50,
    "squeeze":     0.47,
    "insider":     0.44,
}

# Base win rate of the simulated book, with a slow upward drift so the win-rate
# curve and threshold nudges visibly respond as the bot "improves".
_BASE_WIN_RATE = 0.46
_WIN_DRIFT     = 0.12   # added linearly across the run


def _score_for(agent: str, won: bool, direction: str, rng: random.Random) -> float:
    """Map an agent's (latent) correctness to a plausible 0-100 score.

    The tuner reads a LONG agent as "agreed" when score >= 50 and a SHORT agent
    as "agreed" when score <= 50. We pick whether the agent agreed with the
    winning side using its skill, then sample a score on the right side of 50.
    """
    skill = _AGENT_SKILL.get(agent, 0.5)
    correct = rng.random() < skill
    agreed_with_outcome = correct == won  # if it was right and we won, it agreed; etc.

    bullish_vote = agreed_with_outcome if direction == "LONG" else not agreed_with_outcome
    if bullish_vote:
        return round(rng.uniform(55, 88), 1)
    return round(rng.uniform(12, 45), 1)


def _build_trades(n: int, rng: random.Random) -> list[dict]:
    """Generate n closed trades in api_server shape (direction/pnl/evaluations)."""
    trades: list[dict] = []
    start = datetime.now(timezone.utc) - timedelta(days=35)
    t = start
    for i in range(n):
        direction = "LONG" if rng.random() < 0.68 else "SHORT"
        win_rate = _BASE_WIN_RATE + _WIN_DRIFT * (i / max(1, n - 1))
        won = rng.random() < win_rate
        # Realistic asymmetric P&L: winners run a bit, losers cut near the stop.
        pnl = round(rng.uniform(60, 480) if won else -rng.uniform(40, 300), 2)
        evaluations = [
            {"role": agent, "score": _score_for(agent, won, direction, rng)}
            for agent in _AGENT_SKILL
        ]
        t += timedelta(hours=rng.uniform(2, 14))
        trades.append({
            "status": "closed",
            "direction": direction,
            "pnl": pnl,
            "closed_at": t.isoformat(),
            "evaluations": evaluations,
        })
    return trades


def _tag_simulated() -> None:
    """Re-stamp the tuner's output files with simulated=true for honest UI."""
    if _WEIGHTS_FILE.exists():
        try:
            w = json.loads(_WEIGHTS_FILE.read_text())
            w["simulated"] = True
            _WEIGHTS_FILE.write_text(json.dumps(w, indent=2), encoding="utf-8")
        except Exception:
            pass
    if _HISTORY_FILE.exists():
        lines = []
        for line in _HISTORY_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
                snap["simulated"] = True
                lines.append(json.dumps(snap))
            except json.JSONDecodeError:
                continue
        _HISTORY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_simulation(n_trades: int = 140, seed: int = 7) -> dict:
    """Generate trades, drive the real WeightTuner step-by-step, tag, summarise.

    Stepping one trade at a time (once past the warm-up minimum) yields a smooth
    history of genuine tuner snapshots rather than a single endpoint.
    """
    rng = random.Random(seed)
    trades = _build_trades(n_trades, rng)

    # Fresh start: clear any prior simulated/real artefacts so curves are clean.
    _HISTORY_FILE.parent.mkdir(exist_ok=True)
    if _HISTORY_FILE.exists():
        _HISTORY_FILE.unlink()

    tuner = WeightTuner(AgentWeights().as_map())
    # Feed a growing prefix so the tuner emits one snapshot per new closed trade.
    for i in range(weight_tuner._MIN_TRADES, len(trades) + 1):
        tuner.update_from_trades(trades[:i])

    _tag_simulated()

    steps = sum(1 for _ in _HISTORY_FILE.read_text().splitlines()) if _HISTORY_FILE.exists() else 0
    return {"trades": len(trades), "steps": steps, "simulated": True}


def main() -> None:
    result = run_simulation()
    print(f"Simulated learning seeded: {result['trades']} trades → "
          f"{result['steps']} tuning steps")
    print(f"  weights : {_WEIGHTS_FILE}")
    print(f"  history : {_HISTORY_FILE}")


if __name__ == "__main__":
    main()
