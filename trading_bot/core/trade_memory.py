"""Decision memory — the bot's reflection log.

Pattern borrowed from TauricResearch/TradingAgents: every directional decision
is remembered, and once the position closes its realised P&L is attached. The
DecisionAgent then injects a compact "here's how your own recent calls turned
out" block into its next prompt so it can learn from its track record instead
of evaluating each ticker in a vacuum.

File-backed (``data/decision_memory.json``) so the DecisionAgent (which records
decisions) and the PortfolioManager (which records outcomes when it detects an
exit) can share state without being wired together. All operations swallow
errors — the memory is an advisory hint and must NEVER interfere with trading.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).parent.parent / "data" / "decision_memory.json"


class TradeMemory:
    def __init__(self, path: Optional[Path] = None, *, max_entries: int = 120) -> None:
        self.path = Path(path) if path else _DEFAULT_PATH
        self.max_entries = max_entries

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> list[dict]:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except Exception:
            logger.debug("decision memory read failed", exc_info=True)
        return []

    def _save(self, entries: list[dict]) -> None:
        try:
            self.path.parent.mkdir(exist_ok=True)
            trimmed = entries[-self.max_entries:]
            self.path.write_text(json.dumps(trimmed, default=str), encoding="utf-8")
        except Exception:
            logger.debug("decision memory write failed", exc_info=True)

    # ── recording ────────────────────────────────────────────────────────────

    def record_decision(
        self,
        ticker: str,
        decision: str,
        composite: float,
        *,
        factors: Optional[list] = None,
        concerns: Optional[list] = None,
    ) -> None:
        """Append a directional decision (LONG/SHORT). PASS is not remembered."""
        if str(decision).upper() not in ("LONG", "SHORT"):
            return
        entries = self._load()
        entries.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "ticker": str(ticker).upper(),
            "decision": str(decision).upper(),
            "composite": round(float(composite), 1),
            "factors": (factors or [])[:2],
            "concerns": (concerns or [])[:1],
            "outcome_pnl": None,
        })
        self._save(entries)

    def record_outcome(self, ticker: str, pnl_usd: float) -> None:
        """Attach a realised P&L to the most recent unresolved decision for ``ticker``."""
        entries = self._load()
        symbol = str(ticker).upper()
        for entry in reversed(entries):
            if entry.get("ticker") == symbol and entry.get("outcome_pnl") is None:
                entry["outcome_pnl"] = round(float(pnl_usd), 2)
                self._save(entries)
                return

    # ── reflection block for the LLM prompt ──────────────────────────────────

    def recent_lessons(self, k: int = 6) -> str:
        """Format the last ``k`` RESOLVED outcomes into a prompt block.

        Returns "" when there is no resolved history yet.
        """
        resolved = [e for e in self._load() if e.get("outcome_pnl") is not None]
        if not resolved:
            return ""
        lines = ["RECENT OUTCOMES (learn from your own track record):"]
        wins = 0
        for e in resolved[-k:]:
            pnl = float(e["outcome_pnl"])
            verdict = "WON" if pnl >= 0 else "LOST"
            wins += 1 if pnl >= 0 else 0
            lines.append(
                f"- {e['ticker']} {e['decision']} (conviction {e['composite']:.0f}) "
                f"→ {verdict} {'+' if pnl >= 0 else ''}{pnl:.0f}"
            )
        shown = resolved[-k:]
        lines.append(
            f"- Net of these {len(shown)}: {wins} won / {len(shown) - wins} lost. "
            "Repeat what worked; be cautious about setups resembling recent losers."
        )
        return "\n".join(lines)
