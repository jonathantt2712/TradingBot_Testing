"""EODReportAgent — end-of-day narrative over the bot's own recorded activity.

Inspired by the ReportAgent pattern in a multi-agent simulation project: rather
than dumping raw state into a prompt, it gathers a *bounded* set of facts from
what the bot actually did today — the decision audit log, realised trade stats,
and the reflection memory — and asks the LLM to narrate them, with the recorded
numbers as the evidence. Falls back to a deterministic summary when no LLM key
is configured, so a report always goes out.

This is the gather-then-summarise form of that pattern: the LLMAdapter has no
native tool-calling, so the facts are collected up front rather than via a ReAct
tool loop — same spirit (small, grounded inputs), far less machinery.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from core import health
from core.llm_adapter import LLMAdapter
from core.trade_memory import TradeMemory
from core.trade_stats import format_block, load_closed_trades, summarize

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
# Same audit file the PortfolioManager appends to (logs/decisions.jsonl).
_AUDIT_FILE = Path(__file__).parents[2] / "logs" / "decisions.jsonl"

_SYSTEM_PROMPT = (
    "You are the end-of-day desk analyst for an algorithmic day-trading bot. "
    "Given the day's recorded activity, write a brief, factual desk note. "
    "Be concrete, cite the numbers, no markdown headers, no preamble."
)


def _read_today_audit(path: Path, day_et: date) -> list[dict]:
    """Return the audit records whose ET trading day matches ``day_et``."""
    records: list[dict] = []
    try:
        if not path.exists():
            return records
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts")
            try:
                when = datetime.fromisoformat(ts) if ts else None
            except ValueError:
                when = None
            if when is None:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when.astimezone(_ET).date() == day_et:
                records.append(rec)
    except Exception:
        logger.debug("audit read failed", exc_info=True)
    return records


def _summarize_audit(records: list[dict]) -> dict:
    """Collapse a day of audit records into the headline facts."""
    decisions = [r for r in records if r.get("type") == "decision"]
    fills = [r for r in records if r.get("type") == "fill"]
    by_dir = {"LONG": 0, "SHORT": 0, "PASS": 0}
    executed: list[str] = []
    vetoed = 0
    for r in decisions:
        d = str(r.get("decision", "PASS")).upper()
        by_dir[d] = by_dir.get(d, 0) + 1
        if r.get("executed"):
            executed.append(str(r.get("ticker", "")))
        if any(a.get("veto") for a in (r.get("agents") or [])):
            vetoed += 1
    slips = [r.get("slippage_bps") for r in fills if r.get("slippage_bps") is not None]
    avg_slip = round(sum(slips) / len(slips), 1) if slips else None
    return {
        "evaluated": len(decisions),
        "by_direction": by_dir,
        "executed": [t for t in executed if t],
        "vetoed": vetoed,
        "fills": len(fills),
        "avg_slippage_bps": avg_slip,
    }


class EODReportAgent:
    """Builds the end-of-day desk note from the bot's recorded activity."""

    def __init__(
        self,
        *,
        gemini_api_key: str = "",
        anthropic_api_key: str = "",
        audit_file: Optional[Path] = None,
        trades_file: Optional[Path] = None,
        memory: Optional[TradeMemory] = None,
    ) -> None:
        self._llm = LLMAdapter(gemini_key=gemini_api_key, anthropic_key=anthropic_api_key)
        self._audit_file = Path(audit_file) if audit_file else _AUDIT_FILE
        self._trades_file = Path(trades_file) if trades_file else None
        self._memory = memory or TradeMemory()

    def _facts(self, now: datetime) -> tuple[date, dict, dict]:
        day = now.astimezone(_ET).date()
        audit = _summarize_audit(_read_today_audit(self._audit_file, day))
        stats = summarize(load_closed_trades(self._trades_file))
        return day, audit, stats

    def _facts_block(self, day: date, audit: dict, stats: dict) -> str:
        bd = audit["by_direction"]
        lines = [
            f"DATE (ET): {day.isoformat()}",
            f"Tickers evaluated: {audit['evaluated']} "
            f"(LONG {bd.get('LONG', 0)} / SHORT {bd.get('SHORT', 0)} / PASS {bd.get('PASS', 0)})",
            f"Risk vetoes: {audit['vetoed']}",
            "Orders executed: "
            + (", ".join(audit["executed"]) if audit["executed"] else "none"),
        ]
        if audit["fills"]:
            slip = audit["avg_slippage_bps"]
            lines.append(
                f"Fills: {audit['fills']}"
                + (f" | avg slippage {slip:+.1f} bps" if slip is not None else "")
            )
        lines.append(format_block(stats))
        lessons = self._memory.recent_lessons()
        if lessons:
            lines.append(lessons)
        return "\n".join(lines)

    @staticmethod
    def _deterministic(day: date, audit: dict, stats: dict) -> str:
        bd = audit["by_direction"]
        note = (
            f"EOD report {day.isoformat()} (ET): evaluated {audit['evaluated']}, "
            f"{len(audit['executed'])} executed, {audit['vetoed']} risk vetoes "
            f"({bd.get('LONG', 0)}L/{bd.get('SHORT', 0)}S/{bd.get('PASS', 0)}P)."
        )
        if audit["executed"]:
            note += " Traded: " + ", ".join(audit["executed"]) + "."
        if audit["avg_slippage_bps"] is not None:
            note += f" Avg slippage {audit['avg_slippage_bps']:+.1f} bps."
        if stats.get("closed"):
            note += (
                f" History: {stats['closed']} closed, win {stats['win_rate']}%, "
                f"P&L ${stats['total_pnl']:+.2f}."
            )
        return note

    async def generate(self, *, now: Optional[datetime] = None) -> str:
        """Return the day's desk note (LLM narrative, or deterministic fallback)."""
        now = now or datetime.now(timezone.utc)
        day, audit, stats = self._facts(now)
        needs = health.format_block()
        body = None
        if self._llm.has_llm:
            prompt = (
                "Write a concise (<=120 words) end-of-day desk note from these facts. "
                "Cover what traded and why the rest did not, slippage, and any win-rate "
                "trend. Plain prose.\n\n" + self._facts_block(day, audit, stats)
                + (f"\n\n{needs}" if needs else "")
            )
            try:
                text = await self._llm.chat(prompt, system=_SYSTEM_PROMPT)
                if text:
                    body = text.strip()
            except Exception:
                logger.warning("EOD report LLM failed — using deterministic summary", exc_info=True)
        if body is None:
            body = self._deterministic(day, audit, stats)
        # Always append the explicit needs block so it's never lost to the LLM.
        return f"{body}\n\n{needs}" if needs else body
