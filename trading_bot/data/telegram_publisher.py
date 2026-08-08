"""Telegram notification publisher.

Sends trade alerts by calling the dashboard's internal notify endpoint
(POST {DASHBOARD_URL}/api/internal/telegram/notify).

Two hard rules live here, so every caller inherits them:
  * only actual buys and sells are pushed (entry / exit) — no scanner
    chatter, gap lists, weekly digests or health nags;
  * nothing is pushed while the US equities market is closed.

All subscriber data lives in PostgreSQL (via Prisma in the dashboard).
No local file storage — works on Railway without persistent volumes.

Config (env vars):
    DASHBOARD_URL    — Next.js deployment URL (e.g. https://your-app.vercel.app)
    BOT_API_SECRET   — shared secret for bot ↔ dashboard auth (optional but recommended)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo as _ZoneInfo
    _ET = _ZoneInfo("America/New_York")
except ImportError:  # pragma: no cover — stdlib on every supported version
    _ET = None


def market_is_open() -> bool:
    """True during the US equities regular session (Mon–Fri 09:30–16:00 ET).

    Same window api_server._is_market_open uses. When the timezone database is
    unavailable we can't tell, so we allow the send rather than swallow a fill.
    """
    if _ET is None:
        return True
    now = datetime.now(_ET)
    if now.weekday() >= 5:                        # Saturday=5, Sunday=6
        return False
    open_t  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_t <= now <= close_t


class TelegramPublisher:
    """Posts trade entry/exit payloads to the dashboard's Telegram notify endpoint."""

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
        if not market_is_open():
            logger.info("Telegram %s suppressed — market closed", payload.get("type"))
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
                    else:
                        logger.info("Telegram notify OK: type=%s", payload.get("type"))
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
