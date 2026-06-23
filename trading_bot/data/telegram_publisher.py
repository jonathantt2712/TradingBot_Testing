"""Telegram notification publisher — multi-user, per-event alerts.

Subscribers are stored in data/telegram_subscribers.json:
  { "user@email.com": { "chat_id": "123", "activated_at": "ISO" } }

Pending link tokens in data/telegram_tokens.json:
  { "UUID": { "email": "...", "created_at": "ISO" } }

Bot username is fetched once from Telegram on first use.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

_TELEGRAM_API  = "https://api.telegram.org/bot{token}/{method}"
_TOKEN_EXPIRY_MIN = 10   # link tokens expire after 10 minutes
_DATA_DIR = Path(__file__).parent.parent / "data"


def _tg_url(token: str, method: str) -> str:
    return _TELEGRAM_API.format(token=token, method=method)


class TelegramPublisher:
    """Sends formatted alerts to all subscribed Telegram users."""

    def __init__(self, bot_token: str = "") -> None:
        self._token       = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._bot_username: Optional[str] = None
        self._poll_offset = 0
        self._subs_file   = _DATA_DIR / "telegram_subscribers.json"
        self._tok_file    = _DATA_DIR / "telegram_tokens.json"

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _load_subs(self) -> dict:
        try:
            import json
            return json.loads(self._subs_file.read_text()) if self._subs_file.exists() else {}
        except Exception:
            return {}

    def _save_subs(self, data: dict) -> None:
        import json
        self._subs_file.write_text(json.dumps(data, indent=2))

    def _load_tokens(self) -> dict:
        try:
            import json
            return json.loads(self._tok_file.read_text()) if self._tok_file.exists() else {}
        except Exception:
            return {}

    def _save_tokens(self, data: dict) -> None:
        import json
        self._tok_file.write_text(json.dumps(data, indent=2))

    def _all_chat_ids(self) -> list[str]:
        return [v["chat_id"] for v in self._load_subs().values() if v.get("chat_id")]

    # ------------------------------------------------------------------
    # Token management (called by API endpoints)
    # ------------------------------------------------------------------

    def create_link_token(self, email: str) -> str:
        """Generate a one-time link token for this user (valid 10 min)."""
        tokens = self._load_tokens()
        # Remove any expired tokens for this email first
        now = datetime.utcnow()
        tokens = {
            t: v for t, v in tokens.items()
            if v.get("email") != email
            and (now - datetime.fromisoformat(v["created_at"])).total_seconds() < _TOKEN_EXPIRY_MIN * 60
        }
        token = str(uuid.uuid4())
        tokens[token] = {"email": email, "created_at": now.isoformat()}
        self._save_tokens(tokens)
        return token

    def link_status(self, email: str) -> dict:
        subs = self._load_subs()
        entry = subs.get(email)
        if entry:
            return {"linked": True, "activated_at": entry.get("activated_at")}
        return {"linked": False}

    def unlink(self, email: str) -> None:
        subs = self._load_subs()
        subs.pop(email, None)
        self._save_subs(subs)

    # ------------------------------------------------------------------
    # Telegram API helpers
    # ------------------------------------------------------------------

    async def _get(self, method: str, **params) -> Optional[dict]:
        if not self._token:
            return None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    _tg_url(self._token, method),
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    if r.status == 200:
                        return await r.json()
        except Exception as exc:
            logger.debug("Telegram GET %s failed: %s", method, exc)
        return None

    async def _send(self, chat_id: str, text: str) -> None:
        if not self._token:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _tg_url(self._token, "sendMessage"),
                    json={
                        "chat_id":    chat_id,
                        "text":       text[:4000],
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as r:
                    if r.status != 200:
                        body = await r.text()
                        logger.warning("Telegram send to %s failed %d: %s", chat_id, r.status, body[:200])
        except Exception as exc:
            logger.warning("Telegram send error: %s", exc)

    async def _broadcast(self, text: str) -> None:
        """Send a message to every subscribed chat."""
        chat_ids = self._all_chat_ids()
        if not chat_ids:
            return
        await asyncio.gather(*[self._send(cid, text) for cid in chat_ids])

    async def fetch_bot_username(self) -> Optional[str]:
        if self._bot_username:
            return self._bot_username
        data = await self._get("getMe")
        if data and data.get("ok"):
            self._bot_username = data["result"].get("username")
        return self._bot_username

    # ------------------------------------------------------------------
    # Telegram update polling — handles /start TOKEN
    # ------------------------------------------------------------------

    async def poll_once(self) -> None:
        """Fetch one batch of updates and process /start commands."""
        data = await self._get("getUpdates", offset=self._poll_offset, timeout=5, limit=20)
        if not data or not data.get("ok"):
            return
        for update in data.get("result", []):
            self._poll_offset = update["update_id"] + 1
            msg = update.get("message") or update.get("edited_message")
            if not msg:
                continue
            text    = (msg.get("text") or "").strip()
            chat_id = str(msg["chat"]["id"])
            if text.startswith("/start"):
                parts = text.split(maxsplit=1)
                token = parts[1].strip() if len(parts) > 1 else ""
                await self._handle_start(chat_id, token)

    async def _handle_start(self, chat_id: str, token: str) -> None:
        tokens = self._load_tokens()
        entry  = tokens.get(token)
        if not entry:
            await self._send(chat_id, (
                "⚠️ <b>Invalid or expired link.</b>\n\n"
                "Please go back to your profile page and click "
                "<b>Connect Telegram</b> again to get a fresh link."
            ))
            return

        # Check token age
        created = datetime.fromisoformat(entry["created_at"])
        if (datetime.utcnow() - created).total_seconds() > _TOKEN_EXPIRY_MIN * 60:
            tokens.pop(token, None)
            self._save_tokens(tokens)
            await self._send(chat_id, (
                "⏰ <b>Link expired.</b>\n\n"
                "Please go back to your profile page and connect again — "
                "the link is valid for 10 minutes."
            ))
            return

        email = entry["email"]
        # Save subscriber
        tokens.pop(token, None)
        self._save_tokens(tokens)
        subs = self._load_subs()
        subs[email] = {"chat_id": chat_id, "activated_at": datetime.utcnow().isoformat()}
        self._save_subs(subs)

        logger.info("Telegram: linked %s → chat_id %s", email, chat_id)
        await self._send_welcome(chat_id)

    # ------------------------------------------------------------------
    # Welcome message
    # ------------------------------------------------------------------

    async def _send_welcome(self, chat_id: str) -> None:
        text = (
            "👋 <b>Welcome to TradingBot Alerts!</b>\n\n"
            "You're all set. Here's what I'll keep you updated on:\n\n"
            "📥 <b>Trade Entry</b> — whenever the bot enters a position: "
            "the ticker, direction, entry price, target, stop, and the reasoning behind the trade.\n\n"
            "📤 <b>Trade Exit</b> — when we close a position: "
            "exit price, profit/loss, and why we got out.\n\n"
            "📡 <b>Market Events</b> — if something notable happens in the market "
            "(regime shift, unusual volatility, big move), you'll get a heads-up.\n\n"
            "📊 <b>Weekly Summary</b> — every Monday morning, a recap of the week's "
            "trades, P&amp;L, win rate, and overall bot performance.\n\n"
            "Stay sharp, and let's make some money 🚀"
        )
        await self._send(chat_id, text)

    # ------------------------------------------------------------------
    # Trade notifications
    # ------------------------------------------------------------------

    async def send_trade_entry(self, trade: dict[str, Any]) -> None:
        """Notify all subscribers when a new trade is entered."""
        if not self.enabled:
            return
        ticker    = trade.get("ticker", "?")
        direction = trade.get("direction", "LONG")
        entry     = trade.get("entry", 0.0)
        stop      = trade.get("stop_loss", 0.0)
        target    = trade.get("take_profit", 0.0)
        qty       = trade.get("qty", 0)
        score     = trade.get("composite_score", 0.0)
        rationale = trade.get("rationale", "")
        rr        = round(abs(target - entry) / max(abs(entry - stop), 0.01), 2) if entry and stop else 0
        total_cost = qty * entry
        risk_per_share = abs(entry - stop)
        dollar_risk    = qty * risk_per_share
        expected_gain  = qty * abs(target - entry)

        arrow = "🟢" if direction == "LONG" else "🔴"
        dir_word = "Long" if direction == "LONG" else "Short"

        lines = [
            f"{arrow} <b>New Trade — {dir_word} {ticker}</b>",
            "",
            f"📌 <b>Entry:</b> ${entry:.2f}   |   <b>Qty:</b> {qty} shares   |   <b>Total:</b> ${total_cost:,.0f}",
            f"🎯 <b>Target:</b> ${target:.2f}   |   <b>Stop:</b> ${stop:.2f}",
            f"⚖️ <b>R/R:</b> {rr:.2f}x   |   <b>Risk:</b> ${dollar_risk:.0f}   |   <b>Upside:</b> ${expected_gain:.0f}",
            f"💡 <b>Score:</b> {score:.0f}/100",
        ]
        if rationale:
            lines += ["", f"📝 {rationale[:200]}"]

        await self._broadcast("\n".join(lines))

    async def send_trade_exit(self, trade: dict[str, Any], exit_price: float,
                               reason: str, pnl: Optional[float] = None) -> None:
        """Notify all subscribers when a position is closed."""
        if not self.enabled:
            return
        ticker    = trade.get("ticker", "?")
        direction = trade.get("direction", "LONG")
        entry     = trade.get("entry", 0.0)
        qty       = trade.get("qty", 0)

        if pnl is None:
            pnl = (exit_price - entry) * qty if direction == "LONG" else (entry - exit_price) * qty

        arrow  = "✅" if pnl >= 0 else "❌"
        pnl_sign = "+" if pnl >= 0 else ""
        pnl_color_word = "profit" if pnl >= 0 else "loss"
        pct_move = ((exit_price - entry) / entry * 100) if entry else 0
        if direction == "SHORT":
            pct_move = -pct_move

        lines = [
            f"{arrow} <b>Position Closed — {ticker}</b>",
            "",
            f"📊 <b>{'Profit' if pnl >= 0 else 'Loss'}:</b> {pnl_sign}${pnl:,.2f}  ({pnl_sign}{pct_move:.2f}%)",
            f"📌 <b>Entry:</b> ${entry:.2f}   →   <b>Exit:</b> ${exit_price:.2f}",
            f"📦 <b>Qty:</b> {qty} shares",
        ]
        if reason:
            friendly = reason.replace("_", " ").capitalize()
            lines += ["", f"📋 <b>Reason:</b> {friendly}"]

        await self._broadcast("\n".join(lines))

    async def send_market_event(self, headline: str, detail: str = "") -> None:
        """Push a notable market event to all subscribers."""
        if not self.enabled:
            return
        lines = [f"📡 <b>Market Update</b>\n\n{headline}"]
        if detail:
            lines.append(f"\n{detail[:300]}")
        await self._broadcast("\n".join(lines))

    async def send_weekly_summary(self, stats: dict[str, Any]) -> None:
        """Send a weekly performance summary to eligible subscribers."""
        if not self.enabled:
            return
        subs = self._load_subs()
        now  = datetime.utcnow()
        cutoff = now - timedelta(days=7)

        for email, info in subs.items():
            try:
                activated = datetime.fromisoformat(info.get("activated_at", ""))
            except (ValueError, TypeError):
                continue
            if activated > cutoff:
                continue  # not yet 1 week since activation

            chat_id = info.get("chat_id")
            if not chat_id:
                continue

            total   = stats.get("total_trades", 0)
            wins    = stats.get("wins", 0)
            losses  = stats.get("losses", 0)
            pnl     = stats.get("total_pnl", 0.0)
            win_rate = (wins / total * 100) if total else 0
            best    = stats.get("best_trade", {})
            worst   = stats.get("worst_trade", {})

            pnl_sign = "+" if pnl >= 0 else ""
            pnl_emoji = "📈" if pnl >= 0 else "📉"

            lines = [
                f"📊 <b>Weekly Summary</b>",
                f"Week of {now.strftime('%b %d, %Y')}",
                "",
                f"{pnl_emoji} <b>Total P&amp;L:</b> {pnl_sign}${pnl:,.2f}",
                f"🏆 <b>Win Rate:</b> {win_rate:.1f}%  ({wins}W / {losses}L / {total} trades)",
            ]
            if best.get("ticker"):
                bp = best.get("pnl", 0)
                lines.append(f"⭐ <b>Best Trade:</b> {best['ticker']} +${bp:,.0f}")
            if worst.get("ticker"):
                wp = worst.get("pnl", 0)
                lines.append(f"💔 <b>Worst Trade:</b> {worst['ticker']} -${abs(wp):,.0f}")

            lines += [
                "",
                "Keep it up — see you next week! 🚀",
            ]
            await self._send(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------
    # Legacy single-chat methods (kept for backwards compatibility)
    # ------------------------------------------------------------------

    async def send_report(self, text: str) -> None:
        if text:
            await self._broadcast(f"<b>📊 EOD Report</b>\n{text[:3900]}")

    async def send_alert(self, lines: list[str]) -> None:
        if lines:
            await self._broadcast(f"<b>⚠️ Bot needs attention</b>\n" + "\n".join(lines)[:3900])

    async def send_gapper_alert(self, gappers: list[dict]) -> None:
        if not gappers:
            return
        parts = ["<b>📈 Pre-Market Gappers</b>"]
        for g in gappers[:5]:
            arrow = "🟢" if g.get("direction") == "LONG" else "🔴"
            line  = f"{arrow} <b>{g.get('ticker','?')}</b>  gap={g.get('gap_pct',0):+.1f}%"
            if g.get("catalyst"):
                line += f"  | {g['catalyst'][:60]}"
            parts.append(line)
        await self._broadcast("\n".join(parts))

    async def send_strategy_alert(self, hits: list[dict]) -> None:
        if not hits:
            return
        parts = ["<b>⚡ Strategy Alert</b>"]
        for h in hits[:5]:
            arrow = "🟢" if h.get("direction") == "LONG" else "🔴"
            line  = (
                f"{arrow} <b>{h.get('ticker','?')}</b>  {h.get('direction','?')}"
                f"  score={h.get('composite_score',0):.0f}"
                f"  entry=${h.get('risk',{}).get('entry',0):.2f}"
            )
            if h.get("rationale"):
                line += f"\n   {h['rationale'][:80]}"
            parts.append(line)
        await self._broadcast("\n".join(parts))
