"""Telegram alert publisher — push scanner hits to a Telegram bot.

Config via environment:
    TELEGRAM_BOT_TOKEN   — bot token from @BotFather
    TELEGRAM_CHAT_ID     — target chat/channel id (e.g. "-1001234567890")

If either value is missing the publisher silently no-ops.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramPublisher:
    """Sends formatted trade alerts to a Telegram chat."""

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
    ) -> None:
        self._token  = bot_token  or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat   = chat_id    or os.getenv("TELEGRAM_CHAT_ID",    "")

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat)

    async def _send(self, text: str) -> None:
        if not self.enabled:
            return
        url = _TELEGRAM_API.format(token=self._token)
        payload = {
            "chat_id":    self._chat,
            "text":       text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as r:
                    if r.status != 200:
                        body = await r.text()
                        logger.warning("Telegram send failed %d: %s", r.status, body[:200])
        except Exception as exc:
            logger.warning("Telegram send error: %s", exc)

    async def send_gapper_alert(self, gappers: list[dict[str, Any]]) -> None:
        """Send a pre-market gapper alert with the top candidates."""
        if not gappers:
            return
        lines = ["<b>📈 Pre-Market Gappers</b>"]
        for g in gappers[:5]:
            ticker    = g.get("ticker", "?")
            gap_pct   = g.get("gap_pct", 0.0)
            price     = g.get("risk", {}).get("entry") or g.get("chg_pct") or 0
            direction = g.get("direction", "LONG")
            catalyst  = g.get("catalyst", "")
            arrow     = "🟢" if direction == "LONG" else "🔴"
            line = f"{arrow} <b>{ticker}</b>  gap={gap_pct:+.1f}%"
            if catalyst:
                line += f"  | {catalyst[:60]}"
            lines.append(line)
        await self._send("\n".join(lines))

    async def send_strategy_alert(self, hits: list[dict[str, Any]]) -> None:
        """Send a market-hours strategy alert for actionable signals."""
        if not hits:
            return
        lines = ["<b>⚡ Strategy Alert</b>"]
        for h in hits[:5]:
            ticker    = h.get("ticker", "?")
            direction = h.get("direction", "?")
            score     = h.get("composite_score", 0.0)
            rationale = h.get("rationale", "")
            entry     = h.get("risk", {}).get("entry", 0)
            arrow     = "🟢" if direction == "LONG" else "🔴"
            line = (
                f"{arrow} <b>{ticker}</b>  {direction}  score={score:.0f}"
                f"  entry=${entry:.2f}"
            )
            if rationale:
                line += f"\n   {rationale[:80]}"
            lines.append(line)
        await self._send("\n".join(lines))
