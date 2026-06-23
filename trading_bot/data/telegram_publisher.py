"""Telegram notification publisher.

Sends trade/market/weekly alerts by calling the dashboard's internal
notify endpoint (POST {DASHBOARD_URL}/api/internal/telegram/notify).

All subscriber data lives in PostgreSQL (via Prisma in the dashboard).
No local file storage — works on Railway without persistent volumes.

Config (env vars):
    DASHBOARD_URL    — Next.js deployment URL (e.g. https://your-app.vercel.app)
    BOT_API_SECRET   — shared secret for bot ↔ dashboard auth (optional but recommended)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class TelegramPublisher:
    """Posts notification payloads to the dashboard's Telegram notify endpoint."""

    def __init__(self, bot_token: str = "") -> None:
        self._token        = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._dashboard    = os.getenv("DASHBOARD_URL", "").rstrip("/")
        self._secret       = os.getenv("BOT_API_SECRET", "")

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._dashboard)

    async def _notify(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        url     = f"{self._dashboard}/api/internal/telegram/notify"
        headers = {"Content-Type": "application/json"}
        if self._secret:
            headers["x-bot-secret"] = self._secret
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    if r.status not in (200, 201):
                        body = await r.text()
                        logger.warning("Telegram notify failed %d: %s", r.status, body[:200])
        except Exception as exc:
            logger.warning("Telegram notify error: %s", exc)

    async def send_trade_entry(self, trade: dict[str, Any]) -> None:
        await self._notify({"type": "trade_entry", "data": trade})

    async def send_trade_exit(self, trade: dict[str, Any], exit_price: float,
                              reason: str, pnl: Optional[float] = None) -> None:
        await self._notify({"type": "trade_exit", "data": {
            **trade,
            "exit_price": exit_price,
            "reason":     reason,
            "pnl":        pnl,
        }})

    async def send_market_event(self, headline: str, detail: str = "") -> None:
        await self._notify({"type": "market_event", "data": {"headline": headline, "detail": detail}})

    async def send_weekly_summary(self, stats: dict[str, Any]) -> None:
        await self._notify({"type": "weekly_summary", "data": stats})

    # ------------------------------------------------------------------
    # Legacy methods — kept for backward compatibility with scanner hooks
    # ------------------------------------------------------------------

    async def send_report(self, text: str) -> None:
        if text:
            await self.send_market_event("EOD Report", text[:500])

    async def send_alert(self, lines: list[str]) -> None:
        if lines:
            await self.send_market_event("⚠️ Bot needs attention", "\n".join(lines)[:500])

    async def send_gapper_alert(self, gappers: list[dict]) -> None:
        if not gappers:
            return
        parts = ["Pre-Market Gappers:"]
        for g in gappers[:5]:
            arrow = "🟢" if g.get("direction") == "LONG" else "🔴"
            parts.append(f"{arrow} {g.get('ticker','?')}  gap={g.get('gap_pct',0):+.1f}%")
        await self.send_market_event("📈 Pre-Market Gappers", "\n".join(parts))

    async def send_strategy_alert(self, hits: list[dict]) -> None:
        if not hits:
            return
        parts = []
        for h in hits[:5]:
            arrow = "🟢" if h.get("direction") == "LONG" else "🔴"
            parts.append(
                f"{arrow} {h.get('ticker','?')} {h.get('direction','?')}"
                f" score={h.get('composite_score',0):.0f}"
                f" entry=${h.get('risk',{}).get('entry',0):.2f}"
            )
        await self.send_market_event("⚡ Strategy Alert", "\n".join(parts))
