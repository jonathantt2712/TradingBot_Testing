"""Push trading bot state to the FastAPI dashboard server after each scan cycle.

Call push_scan_results() from main.py / live_runner.py after evaluate_ticker() calls.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional, Any

import aiohttp

logger = logging.getLogger(__name__)

DASHBOARD_URL = os.getenv("DASHBOARD_API_URL", "http://localhost:8000")
PUBLISH = os.getenv("PUBLISH_TO_DASHBOARD", "true").lower() == "true"


def _regime_to_dict(regime) -> Optional[dict]:
    if regime is None:
        return None
    return {
        "regime":      regime.regime.value,
        "vix_level":   regime.vix_level or 0.0,
        "spy_day_chg": regime.spy_day_chg or 0.0,
        "qqq_day_chg": regime.qqq_day_chg or 0.0,
        "rationale":   regime.rationale,
        "timestamp":   datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }


def _decision_to_rec(decision, scan_report=None) -> Optional[dict]:
    """Convert a TradeDecision + optional scan context → recommendation dict."""
    from core.enums import Decision
    if decision.decision is Decision.PASS or not decision.risk:
        return None

    sector = "Other"
    hot    = False
    if scan_report:
        stat = scan_report.stats.get(decision.ticker)
        if stat:
            sector = stat.sector
            hot    = stat.sector_rank == 1

    return {
        "id":              f"{decision.ticker}-{int(datetime.now(timezone.utc).replace(tzinfo=None).timestamp())}",
        "ticker":          decision.ticker,
        "direction":       decision.decision.value,
        "composite_score": decision.composite_score,
        "risk": {
            "entry":       decision.risk.entry,
            "stop_loss":   decision.risk.stop_loss,
            "take_profit": decision.risk.take_profit,
            "qty":         decision.risk.qty,
            "risk_reward": decision.risk.risk_reward,
            "dollar_risk": round(abs(decision.risk.entry - decision.risk.stop_loss) * decision.risk.qty, 2),
        },
        "regime":     "neutral",  # filled by caller
        "sector":     sector,
        "hot_sector": hot,
        "evaluations": [
            {
                "role":       ev.role.value,
                "score":      ev.score,
                "confidence": ev.confidence,
                "rationale":  getattr(ev, "rationale", None),
            }
            for ev in decision.evaluations
        ],
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }


async def _post(session: aiohttp.ClientSession, path: str, data: Any) -> bool:
    try:
        async with session.post(
            f"{DASHBOARD_URL}{path}", json=data, timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            return resp.status < 300
    except Exception as e:
        logger.debug("Dashboard push failed %s: %s", path, e)
        return False


async def push_scan_results(
    decisions:   list,
    regime=None,
    scan_report=None,
) -> None:
    """Push recommendations + regime to the dashboard API server.

    Call this once per scan cycle, passing all TradeDecision objects.
    Failures are silently ignored — the bot continues regardless.
    """
    if not PUBLISH:
        return

    recs = []
    regime_str = regime.regime.value if regime else "neutral"
    for decision in decisions:
        rec = _decision_to_rec(decision, scan_report)
        if rec:
            rec["regime"] = regime_str
            recs.append(rec)

    regime_dict = _regime_to_dict(regime)

    async with aiohttp.ClientSession() as session:
        tasks = []
        if recs:
            tasks.append(_post(session, "/api/recommendations/update", recs))
        if regime_dict:
            tasks.append(_post(session, "/api/regime/update", regime_dict))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            logger.debug("Dashboard push: recs=%d regime=%s results=%s",
                         len(recs), regime_dict is not None, results)
