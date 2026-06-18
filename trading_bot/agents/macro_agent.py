"""Macro Signal Agent — AI-Trader market-intel macro signals, implemented locally.

Replicates the 4-factor macro regime from the AI-Trader market-intel endpoint
using free Yahoo Finance daily bars (no API key, same aiohttp approach as
RegimeAgent). Fetches once per scan cycle and caches for 30 minutes — all
tickers share the same macro context.

Signals
-------
1. BTC 7-day return          — crypto risk-on/risk-off proxy
2. QQQ 20-day return         — growth equity momentum
3. QQQ vs XLP 20-day spread  — risk-on (growth) vs defensive equity rotation
4. Safe-haven pressure       — max(GLD, UUP) 20-day return; inverse: rising
                               gold/dollar is bearish for risk assets

Score: 50 = neutral; >50 = macro favours LONG; <50 = macro favours SHORT.
Each signal contributes ±8–12 pts (effective range ≈ 18–82).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp

from core.base_agent import NEUTRAL_SCORE, BaseAgent, clamp_score
from core.enums import AgentRole
from core.models import AgentEvaluation, AnalysisContext

logger = logging.getLogger(__name__)

_CACHE_TTL  = 1800  # 30 minutes — daily bars don't move faster than this
_YF_URL     = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_YF_PARAMS  = {"interval": "1d", "range": "3mo"}
_YF_HEADERS = {"User-Agent": "Mozilla/5.0"}

_SYMBOLS = {
    "btc": "BTC-USD",
    "qqq": "QQQ",
    "xlp": "XLP",
    "gld": "GLD",
    "uup": "UUP",
}


def _pct_return(closes: list[float], lookback: int) -> Optional[float]:
    if len(closes) <= lookback:
        return None
    start = closes[-(lookback + 1)]
    if start <= 0:
        return None
    return (closes[-1] / start - 1) * 100


async def _fetch_closes(session: aiohttp.ClientSession, symbol: str) -> Optional[list[float]]:
    try:
        async with session.get(
            _YF_URL.format(symbol=symbol),
            params=_YF_PARAMS,
            headers=_YF_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                logger.debug("YF %s → HTTP %s", symbol, r.status)
                return None
            data = await r.json()
        raw = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        return [c for c in raw if c is not None]
    except Exception as exc:
        logger.debug("YF %s error: %s", symbol, exc)
        return None


def _build_snapshot(
    btc_closes: Optional[list[float]],
    qqq_closes: Optional[list[float]],
    xlp_closes: Optional[list[float]],
    gld_closes: Optional[list[float]],
    uup_closes: Optional[list[float]],
) -> tuple[float, float, str]:
    btc_7d  = _pct_return(btc_closes,  7) if btc_closes else None
    qqq_20d = _pct_return(qqq_closes, 20) if qqq_closes else None
    xlp_20d = _pct_return(xlp_closes, 20) if xlp_closes else None
    gld_20d = _pct_return(gld_closes, 20) if gld_closes else None
    uup_20d = _pct_return(uup_closes, 20) if uup_closes else None

    spread  = (qqq_20d - xlp_20d) if qqq_20d is not None and xlp_20d is not None else None
    sh_vals = [v for v in [gld_20d, uup_20d] if v is not None]
    sh_pres = max(sh_vals) if sh_vals else None

    score   = 50.0
    n       = 0
    parts: list[str] = []

    def _add(val: Optional[float], scale: float, pts: float, label: str, inverse: bool = False) -> None:
        nonlocal score, n
        if val is None:
            return
        n += 1
        contrib = max(-1.0, min(1.0, val / scale)) * pts
        if inverse:
            contrib = -contrib
        score += contrib
        parts.append(f"{label}={val:+.1f}%")

    _add(btc_7d,  5.0,  8.0, "BTC_7d")
    _add(qqq_20d, 5.0, 12.0, "QQQ_20d")
    _add(spread,  5.0, 12.0, "spread")
    _add(sh_pres, 5.0,  8.0, "safe_haven", inverse=True)

    score = clamp_score(score)
    conf  = 0.60 if n >= 3 else 0.40 if n >= 2 else 0.20

    lean  = "bullish" if score >= 55 else "bearish" if score <= 45 else "neutral"
    body  = " | ".join(parts) if parts else "no data"
    return score, conf, f"macro {lean}: {body}"


class MacroSignalAgent(BaseAgent):
    """Macro context agent — AI-Trader market-intel signals via Yahoo Finance."""

    role = AgentRole.MACRO

    def __init__(self, *, weight: float = 0.10) -> None:
        super().__init__(weight=weight)
        self._snapshot: Optional[tuple[float, float, str]] = None
        self._snapshot_ts: float = 0.0
        self._lock = asyncio.Lock()

    async def _refresh(self) -> tuple[float, float, str]:
        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
            ) as session:
                results = await asyncio.gather(
                    _fetch_closes(session, _SYMBOLS["btc"]),
                    _fetch_closes(session, _SYMBOLS["qqq"]),
                    _fetch_closes(session, _SYMBOLS["xlp"]),
                    _fetch_closes(session, _SYMBOLS["gld"]),
                    _fetch_closes(session, _SYMBOLS["uup"]),
                    return_exceptions=True,
                )
        except Exception as exc:
            logger.warning("MacroAgent fetch failed: %s", exc)
            return NEUTRAL_SCORE, 0.1, "macro: fetch error → neutral"

        def _safe(r) -> Optional[list[float]]:
            return r if isinstance(r, list) else None

        snap = _build_snapshot(_safe(results[0]), _safe(results[1]),
                               _safe(results[2]), _safe(results[3]), _safe(results[4]))
        logger.info("MacroAgent snapshot: score=%.1f conf=%.2f — %s", *snap)
        return snap

    async def _get_snapshot(self) -> tuple[float, float, str]:
        now = time.monotonic()
        if self._snapshot is not None and now - self._snapshot_ts < _CACHE_TTL:
            return self._snapshot
        async with self._lock:
            if self._snapshot is not None and time.monotonic() - self._snapshot_ts < _CACHE_TTL:
                return self._snapshot
            self._snapshot = await self._refresh()
            self._snapshot_ts = time.monotonic()
            return self._snapshot

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        if ctx.backtest_mode:
            # Macro signals are point-in-time current data (today's BTC/QQQ/etc).
            # Applying them to historical windows would inject look-ahead bias.
            return AgentEvaluation(
                role=self.role, score=NEUTRAL_SCORE, confidence=0.0,
                rationale="macro: neutral in backtest (point-in-time data, no look-ahead)",
            )
        score, confidence, rationale = await self._get_snapshot()
        return AgentEvaluation(
            role=self.role,
            score=score,
            confidence=confidence,
            rationale=rationale,
        )
