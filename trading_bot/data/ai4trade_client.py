"""AI4Trade REST client — auth, heartbeat, and base requests.

Handles registration, login, token refresh, and the heartbeat polling loop.
All other modules (SocialAgent, SignalPublisher, ChallengeRunner) import
this client rather than talking to the API directly.

Environment variables:
    AI4TRADE_EMAIL     — bot account email (created once, reused)
    AI4TRADE_PASSWORD  — bot account password
    AI4TRADE_BOT_NAME  — display name on the platform (default: tradingbot2026)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

BASE = "https://ai4trade.ai/api"
_TIMEOUT = aiohttp.ClientTimeout(total=15.0)


class AI4TradeClient:
    """Thin async wrapper around the AI4Trade REST API."""

    def __init__(
        self,
        email: str = "",
        password: str = "",
        bot_name: str = "tradingbot2026",
    ) -> None:
        self.email = email or os.environ.get("AI4TRADE_EMAIL", "")
        self.password = password or os.environ.get("AI4TRADE_PASSWORD", "")
        self.bot_name = bot_name or os.environ.get("AI4TRADE_BOT_NAME", "tradingbot2026")
        self.token: str = ""
        self.agent_id: Optional[int] = None
        self._session: Optional[aiohttp.ClientSession] = None

    # --- session lifecycle --------------------------------------------

    async def __aenter__(self) -> "AI4TradeClient":
        self._session = aiohttp.ClientSession(timeout=_TIMEOUT)
        if self.email and self.password:
            await self._authenticate()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._session:
            await self._session.close()

    async def _authenticate(self) -> None:
        """Login, or register then login if first time."""
        # Try login first
        ok = await self._login()
        if not ok:
            await self._register()
            await self._login()

    async def _register(self) -> None:
        try:
            async with self._session.post(f"{BASE}/claw/agents/selfRegister", json={
                "name": self.bot_name,
                "email": self.email,
                "password": self.password,
            }) as resp:
                data = await resp.json()
                if data.get("success"):
                    self.token = data["token"]
                    self.agent_id = data.get("agent_id")
                    logger.info("AI4Trade: registered as %s (id=%s)", self.bot_name, self.agent_id)
        except Exception:
            logger.exception("AI4Trade registration failed")

    async def _login(self) -> bool:
        try:
            async with self._session.post(f"{BASE}/claw/agents/login", json={
                "email": self.email,
                "password": self.password,
            }) as resp:
                data = await resp.json()
                if data.get("token"):
                    self.token = data["token"]
                    self.agent_id = data.get("agent_id") or data.get("id")
                    logger.info("AI4Trade: logged in (id=%s)", self.agent_id)
                    return True
        except Exception:
            logger.debug("AI4Trade login failed (will try register)")
        return False

    # --- helpers ------------------------------------------------------

    @property
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def get(self, path: str, **params: Any) -> dict:
        try:
            async with self._session.get(
                f"{BASE}{path}", headers=self._headers, params=params or None
            ) as resp:
                return await resp.json()
        except Exception:
            logger.debug("AI4Trade GET %s failed", path)
            return {}

    async def post(self, path: str, body: dict) -> dict:
        try:
            async with self._session.post(
                f"{BASE}{path}", headers=self._headers, json=body
            ) as resp:
                return await resp.json()
        except Exception:
            logger.debug("AI4Trade POST %s failed", path)
            return {}

    # --- heartbeat ----------------------------------------------------

    async def heartbeat(self) -> dict:
        """Pull unread messages and tasks from the platform."""
        return await self.post("/claw/agents/heartbeat", {})

    async def heartbeat_loop(
        self,
        callback,  # async callable(messages, tasks)
        *,
        default_interval: float = 30.0,
    ) -> None:
        """Poll heartbeat continuously, calling callback on each batch.

        callback(messages: list, tasks: list) — called with whatever
        the platform returns. Runs until cancelled.
        """
        logger.info("AI4Trade heartbeat loop started")
        while True:
            try:
                data = await self.heartbeat()
                messages = data.get("messages", [])
                tasks = data.get("tasks", [])
                if messages or tasks:
                    await callback(messages, tasks)
                # Drain if platform says there are more messages
                while data.get("has_more_messages") or data.get("has_more_tasks"):
                    data = await self.heartbeat()
                    await callback(data.get("messages", []), data.get("tasks", []))
                interval = data.get("recommended_poll_interval_seconds", default_interval)
            except asyncio.CancelledError:
                logger.info("AI4Trade heartbeat loop cancelled")
                return
            except Exception:
                logger.exception("AI4Trade heartbeat error — retrying in %ss", default_interval)
                interval = default_interval
            await asyncio.sleep(interval)

    # --- signal feed (public, no auth needed) -------------------------

    async def get_signal_feed(
        self,
        *,
        symbol: str = "",
        limit: int = 30,
        message_type: str = "",
        sort: str = "active",
    ) -> list[dict]:
        params: dict = {"limit": limit, "sort": sort}
        if symbol:
            params["symbol"] = symbol
        if message_type:
            params["message_type"] = message_type
        data = await self.get("/signals/feed", **params)
        return data.get("signals", [])

    # --- market intel -------------------------------------------------

    async def get_market_intel(self, symbol: str = "") -> list[dict]:
        """Fetch market intelligence snapshots for a symbol."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = await self.get("/market-intel", **params)
        return data.get("events", data.get("items", []))

    # --- publishing ---------------------------------------------------

    async def publish_trade(
        self,
        *,
        market: str,
        action: str,
        symbol: str,
        price: float,
        quantity: float,
        content: str = "",
        executed_at: str = "now",
    ) -> dict:
        return await self.post("/signals/realtime", {
            "market": market,
            "action": action,
            "symbol": symbol,
            "price": price,
            "quantity": quantity,
            "content": content,
            "executed_at": executed_at,
        })

    async def publish_strategy(
        self,
        *,
        market: str,
        title: str,
        content: str,
        symbols: list[str],
        tags: list[str] | None = None,
    ) -> dict:
        return await self.post("/signals/strategy", {
            "market": market,
            "title": title,
            "content": content,
            "symbols": symbols,
            "tags": tags or [],
        })

    # --- challenges ---------------------------------------------------

    async def list_challenges(self, *, status: str = "active", market: str = "all") -> list[dict]:
        data = await self.get("/challenges", status=status, market=market, limit=50)
        return data.get("challenges", [])

    async def join_challenge(self, challenge_key: str) -> dict:
        return await self.post(f"/challenges/{challenge_key}/join", {})

    async def submit_challenge_trade(
        self,
        challenge_key: str,
        *,
        side: str,
        symbol: str,
        price: float,
        quantity: float,
        content: str = "",
    ) -> dict:
        return await self.post(f"/challenges/{challenge_key}/trade", {
            "side": side,
            "symbol": symbol,
            "price": price,
            "quantity": quantity,
            "content": content,
        })

    async def get_challenge_portfolio(self, challenge_key: str) -> dict:
        data = await self.get(f"/challenges/{challenge_key}/portfolio")
        return data.get("portfolio", {})

    async def get_challenge_leaderboard(self, challenge_key: str) -> list[dict]:
        data = await self.get(f"/challenges/{challenge_key}/leaderboard")
        return data.get("leaderboard", [])

    async def get_my_challenges(self) -> list[dict]:
        data = await self.get("/challenges/me")
        return data.get("challenges", [])

    # --- account ------------------------------------------------------

    async def get_me(self) -> dict:
        return await self.get("/claw/agents/me")
