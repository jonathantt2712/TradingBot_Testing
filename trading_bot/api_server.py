"""FastAPI server — exposes trading bot state to the Next.js dashboard.

Usage:
    pip install fastapi uvicorn aiohttp python-dotenv pandas numpy
    python api_server.py

Endpoints:
    GET  /api/recommendations   Active trade signals
    GET  /api/history           Executed trades
    GET  /api/pnl               Daily P&L series
    GET  /api/stats             Portfolio summary stats
    GET  /api/regime            Current market regime
    GET  /api/sectors           Sector scores
    POST /api/execute           Record an executed trade (called by dashboard)
    POST /api/scan              Trigger a live market scan immediately
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from dotenv import load_dotenv
load_dotenv()

try:
    from zoneinfo import ZoneInfo as _ZoneInfo
    _ET = _ZoneInfo("America/New_York")
except ImportError:
    try:
        from backports.zoneinfo import ZoneInfo as _ZoneInfo  # type: ignore
        _ET = _ZoneInfo("America/New_York")
    except ImportError:
        _ET = None  # fallback — market hours guard disabled


def _is_market_open() -> bool:
    """Return True if US equities market is currently open (Mon–Fri 09:30–16:00 ET)."""
    if _ET is None:
        return True  # can't check — allow through
    now = datetime.now(_ET)
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    open_t  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_t <= now <= close_t


def _next_market_open() -> datetime:
    """Return the next US equities market open (09:30 ET Mon–Fri) as a naive UTC datetime."""
    if _ET is None:
        return datetime.utcnow() + timedelta(hours=1)
    now = datetime.now(_ET)
    candidate = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:  # Saturday=5, Sunday=6
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc).replace(tzinfo=None)


MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
DAILY_LOSS_LIMIT_PCT    = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.02"))   # 2% daily drawdown halt
MAX_CONSECUTIVE_LOSSES  = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))       # 3 consecutive losses halt
TRAIL_STOP_PCT = float(os.getenv("TRAIL_STOP_PCT", "0.05"))  # 5% trailing distance
PORTFOLIO_BETA_CAP = float(os.getenv("PORTFOLIO_BETA_CAP", "2.0"))  # max net |beta| across open positions

# Position exit monitor
EXIT_MONITOR_INTERVAL_MIN = int(os.getenv("EXIT_MONITOR_INTERVAL_MIN", "5"))    # how often to re-score open positions
EXIT_SCORE_THRESHOLD      = float(os.getenv("EXIT_SCORE_THRESHOLD", "40.0"))    # exit LONG if score < this; exit SHORT if score > (100 - this)
ALLOW_OVERNIGHT           = os.getenv("ALLOW_OVERNIGHT", "false").lower() in ("1", "true", "yes")
EOD_REVIEW_MIN_BEFORE     = int(os.getenv("EOD_REVIEW_MIN_BEFORE", "25"))       # minutes before 16:00 ET to run EOD review

# Daily scan stats (reset at midnight by _background_loop)
_scan_stats: Dict[str, Any] = {
    "date":             "",
    "scans_today":      0,
    "tickers_scanned":  0,
    "recs_generated":   0,
    "recs_skipped":     0,
    "scan_errors":      0,
    "last_scan_at":     None,
    "market_closed_skips": 0,
}

import secrets as _secrets
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

_BOT_API_SECRET = os.getenv("BOT_API_SECRET", "")


async def _verify_bot_secret(x_bot_secret: str = Header(default="")) -> None:
    if not _BOT_API_SECRET:
        return  # secret not configured — open in dev; Railway sets it in prod
    if not _secrets.compare_digest(x_bot_secret, _BOT_API_SECRET):
        raise HTTPException(status_code=401, detail="Invalid bot secret")

logger = logging.getLogger("api_server")
logging.basicConfig(level=logging.INFO)

# === Agent imports (lazy -- fallback to simple formula if unavailable) ===

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_AGENTS_AVAILABLE = False
_pm                = None   # PortfolioManager — the SAME composition live/backtest use
_ai4trade_client   = None
_Decision          = None
_EXIT_DECISIONS: list = []  # rolling log of exit-monitor and EOD review decisions
_MAX_EXIT_LOG     = 500
_telegram          = None   # TelegramPublisher — optional push notifications

try:
    import pandas as pd
    import numpy as np
    from bootstrap import build_manager
    from config.settings import load_settings
    from core.models import AnalysisContext
    from core.enums import Decision
    from data.ai4trade_client import AI4TradeClient as _AI4TC
    from data.telegram_publisher import TelegramPublisher as _TelegramPublisher

    _ai4trade_client = _AI4TC(
        email=os.getenv("AI4TRADE_EMAIL", ""),
        password=os.getenv("AI4TRADE_PASSWORD", ""),
    )
    _settings = load_settings()
    _pm = build_manager(_settings, broker=None, ai4=_ai4trade_client)
    # Dashboard scans fetch ~100-bar windows (vs 200 live) — keep the lower
    # bar requirement this endpoint has always used.
    _pm.technical.min_bars = 30
    _Decision = Decision
    _AGENTS_AVAILABLE = True
    _telegram = _TelegramPublisher(
        bot_token=_settings.telegram_bot_token,
        chat_id=_settings.telegram_chat_id,
    )
    logger.info("Agent pipeline loaded via bootstrap.build_manager — unified with live/backtest")

except Exception as _import_err:
    logger.warning("Agent imports failed (%s) -- scanner using fallback formula", _import_err)


async def _evaluate(ctx: "AnalysisContext"):
    """Run the unified PortfolioManager pipeline on one ticker.

    Same agents, weights, and composite as live trading and backtests
    (bootstrap.build_manager). Renders the chart for the vision agent,
    ensures the AI4Trade session is open for the social agent, and bounds
    total evaluation time so a slow LLM can't stall the scan.

    Returns a TradeDecision, or None on failure/timeout.
    """
    if not _AGENTS_AVAILABLE or _pm is None:
        return None

    # Build chart image path for VisionAgent (render async in thread)
    chart_path = None
    if _pm.vision is not None and ctx.bars is not None:
        try:
            from data.chart_renderer import render_chart
            chart_path = await asyncio.to_thread(render_chart, ctx.ticker, ctx.bars)
            ctx = type(ctx)(
                ticker=ctx.ticker,
                bars=ctx.bars,
                account=ctx.account,
                chart_image_path=chart_path,
            )
        except Exception:
            pass

    # Ensure AI4Trade client session is open (social agent)
    if _ai4trade_client is not None and _ai4trade_client._session is None:
        try:
            _ai4trade_client._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15.0)
            )
        except Exception:
            pass

    try:
        return await asyncio.wait_for(_pm.decide(ctx), timeout=30.0)
    except Exception as e:
        logger.debug("decide() failed for %s: %s", ctx.ticker, e)
        return None
    finally:
        if chart_path:
            try:
                os.unlink(chart_path)
            except Exception:
                pass


# === Alpaca credentials & constants ===

_ALPACA_KEY    = os.getenv("ALPACA_API_KEY_ID", "")
_ALPACA_SECRET = os.getenv("ALPACA_API_SECRET", "")
_ALPACA_HEADERS = {
    "APCA-API-KEY-ID":     _ALPACA_KEY,
    "APCA-API-SECRET-KEY": _ALPACA_SECRET,
}

_BROKER_BASE = "https://paper-api.alpaca.markets"
_DATA_BASE   = "https://data.alpaca.markets"

_SECTOR_MAP: Dict[str, str] = {
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "GOOGL": "Technology", "META": "Technology", "AMZN": "Consumer",
    "TSLA": "Consumer", "AMD": "Technology", "INTC": "Technology",
    "NFLX": "Communication", "JPM": "Financials", "BAC": "Financials",
    "GS": "Financials", "XOM": "Energy", "CVX": "Energy",
    "JNJ": "Healthcare", "PFE": "Healthcare", "UNH": "Healthcare",
}


# === Persistent storage ===

DATA_DIR    = _HERE / "data"
DATA_DIR.mkdir(exist_ok=True)
RECS_FILE    = DATA_DIR / "recommendations.json"
TRADES_FILE  = DATA_DIR / "trades.json"
HISTORY_FILE = TRADES_FILE                        # alias — executed trades = history
PNL_FILE     = DATA_DIR / "pnl.json"
CONTEXT_FILE = DATA_DIR / "context.json"
WEIGHTS_FILE = DATA_DIR / "strategy_weights.json"
REGIME_FILE  = DATA_DIR / "regime.json"
REJECT_LOG      = DATA_DIR / "risk_rejections.jsonl"
SNAPSHOT_LOG    = DATA_DIR / "daily_snapshots.jsonl"
AGENT_PERF_FILE = DATA_DIR / "agent_attribution.json"
EARNINGS_CACHE: Dict[str, Any] = {"blacklist": set(), "updated_at": None}


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _log_rejection(ticker: str, reason: str, score: float, details: dict) -> None:
    """Append a trade rejection record to risk_rejections.jsonl."""
    entry = {
        "ts":              datetime.utcnow().isoformat(),
        "ticker":          ticker,
        "reason":          reason,
        "composite_score": round(score, 1),
        **details,
    }
    try:
        with open(REJECT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.debug("rejection log write failed: %s", exc)


# === Kelly position sizing ===

def _kelly_qty(
    equity: float,
    entry: float,
    stop_loss: float,
    take_profit: float,
    composite_score: float,
) -> int:
    # Fail closed: no verified equity or a degenerate plan -> no size.
    # (qty=0 recs still show direction/levels; they just aren't tradeable.)
    risk_per_share   = abs(entry - stop_loss)
    reward_per_share = abs(take_profit - entry)
    if equity <= 0 or risk_per_share < 0.0001:
        return 0

    b = reward_per_share / risk_per_share
    p = min(max(composite_score / 100.0, 0.05), 0.95)
    q = 1.0 - p
    kelly_f = (b * p - q) / b if b > 0 else 0.0
    if kelly_f <= 0:
        return 0  # negative edge — Kelly says don't bet
    half_kelly = kelly_f / 2

    base_risk   = 0.01 * equity
    scaled_risk = base_risk * (half_kelly / 0.25)
    qty         = int(scaled_risk / risk_per_share)
    max_qty     = int((0.15 * equity) / max(entry, 1))
    return max(0, min(qty, max_qty))


# === Strategy weights ===

DEFAULT_WEIGHTS: Dict[str, Any] = {
    "chg_weight":            4.0,
    "intra_weight":          2.0,
    "min_chg_pct":           0.3,
    "stop_pct":              0.02,
    "tp_pct":                0.05,
    "score_floor":           20,
    "score_ceil":            80,
    "min_score":             40,
    "time_window_minutes":   45,
    "atr_stop_multiple":     2.0,
    "atr_target_multiple":   3.0,
    "update_count":          0,
    "win_rate_30d":          None,
    "long_win_rate":         None,
    "short_win_rate":        None,
    "bias":                  "neutral",
    "last_updated":          "",
}


def _load_weights() -> Dict[str, Any]:
    return {**DEFAULT_WEIGHTS, **_load(WEIGHTS_FILE, {})}


def _log_rejection(ticker: str, reason: str, score: float, details: dict) -> None:
    """Append a trade rejection record to risk_rejections.jsonl."""
    entry = {
        "ts":              datetime.utcnow().isoformat(),
        "ticker":          ticker,
        "reason":          reason,
        "composite_score": round(score, 1),
        **details,
    }
    try:
        with open(REJECT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.debug("rejection log write failed: %s", exc)


def _close_simulated_trade(trade: dict, exit_price: float, reason: str) -> None:
    """Mark a trade as closed in-place (used by trailing stop and EoD flatten)."""
    direction = trade.get("direction", "LONG")
    entry     = float(trade.get("entry") or 0)
    qty       = int(trade.get("qty", 1))
    if direction == "LONG":
        pnl     = (exit_price - entry) * qty
        pnl_pct = (exit_price - entry) / entry * 100 if entry else 0.0
    else:
        pnl     = (entry - exit_price) * qty
        pnl_pct = (entry - exit_price) / entry * 100 if entry else 0.0
    trade["status"]      = "closed"
    trade["exit"]        = round(exit_price, 2)
    trade["exit_reason"] = reason
    trade["pnl"]         = round(pnl, 2)
    trade["pnl_pct"]     = round(pnl_pct, 2)
    trade["closed_at"]   = datetime.utcnow().isoformat()


def _save_weights(w: Dict[str, Any]) -> None:
    _save(WEIGHTS_FILE, w)
    logger.info(
        "Weights updated #%d -- win_rate=%.1f%% min_score=%.0f atr_stop=%.2f atr_tp=%.2f",
        w.get("update_count", 0), w.get("win_rate_30d") or 0,
        w.get("min_score", 40), w.get("atr_stop_multiple", 2.0),
        w.get("atr_target_multiple", 3.0),
    )


# === Account equity ===

async def _get_account_equity(session: aiohttp.ClientSession) -> float:
    """Return verified account equity, or 0.0 when unknown (fail closed).

    Never substitute fake equity — sizing code treats 0 as "do not size".
    """
    try:
        async with session.get(
            f"{_BROKER_BASE}/v2/account",
            headers=_ALPACA_HEADERS,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            if r.status == 200:
                data = await r.json()
                return float(data.get("equity") or data.get("cash") or 0.0)
            logger.error("Account equity fetch -> %s: %s", r.status, await r.text())
    except Exception as exc:
        logger.error("Could not fetch account equity — recs will be unsized: %s", exc)
    return 0.0


async def _fetch_earnings_blacklist(session: aiohttp.ClientSession) -> set:
    """Return set of tickers with earnings in the next 48 hours.

    Uses Alpaca corporate actions API. Falls back to empty set on any error
    so the scanner keeps running even if this call fails.
    """
    global EARNINGS_CACHE
    now = datetime.utcnow()
    cached_at = EARNINGS_CACHE.get("updated_at")
    if cached_at and (now - cached_at).total_seconds() < 3600:
        return EARNINGS_CACHE["blacklist"]

    end = (now + timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        async with session.get(
            f"{_DATA_BASE}/v1beta1/corporate-actions/earnings",
            params={"start": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": end, "limit": "200"},
            headers=_ALPACA_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                return EARNINGS_CACHE.get("blacklist", set())
            data = await r.json()
    except Exception:
        return EARNINGS_CACHE.get("blacklist", set())

    blacklist = {item["symbol"] for item in data.get("earnings", []) if item.get("symbol")}
    EARNINGS_CACHE["blacklist"] = blacklist
    EARNINGS_CACHE["updated_at"] = now
    if blacklist:
        logger.info("Earnings blackout: %d tickers skipped (%s…)", len(blacklist), ", ".join(sorted(blacklist)[:5]))
    return blacklist


# === Bar data helpers ===

def _bars_to_df(raw_bars: list) -> Any:
    if not _AGENTS_AVAILABLE or not raw_bars:
        return None
    df = pd.DataFrame(raw_bars).rename(columns={
        "t": "time", "o": "open", "h": "high",
        "l": "low", "c": "close", "v": "volume",
    })
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time").sort_index()
    if not {"open", "high", "low", "close", "volume"}.issubset(df.columns):
        return None
    return df[["open", "high", "low", "close", "volume"]]


async def _fetch_multi_bars(
    session: aiohttp.ClientSession,
    symbols: List[str],
    timeframe: str = "5Min",
    limit: int = 100,
) -> Dict[str, Any]:
    """Fetch recent bars for many symbols.

    The Alpaca multi-symbol bars endpoint applies `limit` to the TOTAL
    number of bars in the response (across all symbols, alphabetically), not
    per symbol -- a symbol early in the alphabet with abundant data can
    consume the whole budget and starve every symbol after it. Fetch each
    symbol individually (concurrently) so every symbol gets up to `limit`
    bars.
    """
    if not _AGENTS_AVAILABLE or not symbols:
        return {}
    start = (datetime.utcnow() - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def _fetch_one(sym: str):
        try:
            async with session.get(
                f"{_DATA_BASE}/v2/stocks/{sym}/bars",
                params={"timeframe": timeframe, "start": start,
                        "limit": str(limit), "feed": "iex"},
                headers=_ALPACA_HEADERS,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                if r.status != 200:
                    logger.warning("Bars fetch for %s returned %s", sym, r.status)
                    return sym, None
                payload = await r.json()
        except Exception as exc:
            logger.warning("Bars fetch for %s failed: %s", sym, exc)
            return sym, None
        return sym, _bars_to_df(payload.get("bars") or [])

    result: Dict[str, Any] = {}
    for sym, df in await asyncio.gather(*(_fetch_one(s) for s in symbols)):
        if df is not None and len(df) > 0:
            result[sym] = df

    return result


async def _fetch_vix_index(session: aiohttp.ClientSession) -> float:
    """Fetch the real CBOE VIX index level.

    Alpaca has no index-data endpoint (only stock/ETF bars), so the actual
    VIX index isn't available from `_fetch_multi_bars`. Yahoo Finance's
    public chart endpoint serves ^VIX without an API key. Returns 0.0 on
    any failure so callers can fall back to the VIXY ETF proxy.
    """
    try:
        async with session.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX",
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                logger.warning("VIX index fetch returned %s", r.status)
                return 0.0
            payload = await r.json()
        closes = payload["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        for c in reversed(closes):
            if c is not None:
                return round(float(c), 1)
    except Exception as exc:
        logger.warning("VIX index fetch failed: %s", exc)
    return 0.0


# === Close trade detection -- bracket order child leg fix (Task #71) ===

async def _check_and_close_trades(session: aiohttp.ClientSession) -> None:
    """
    Parent bracket fill = entry price.
    Exit price lives in the CHILD leg that filled (TP or SL).
    """
    trades      = _load(TRADES_FILE, [])
    open_trades = [t for t in trades if t.get("status") == "open" and t.get("order_id")]
    if not open_trades:
        return

    modified = False
    for trade in open_trades:
        try:
            order_id  = trade["order_id"]
            direction = trade.get("direction", "LONG")
            entry     = float(trade.get("entry") or 0)
            qty       = int(trade.get("qty", 1))
            tp_val    = float(trade.get("take_profit") or (trade.get("risk") or {}).get("take_profit") or 0)
            sl_val    = float(trade.get("stop_loss")   or (trade.get("risk") or {}).get("stop_loss")   or 0)

            # ── Simulated / offline orders (PAPER-* IDs) ──────────────────
            # These have no real Alpaca bracket. Fall back to price-based
            # TP/SL detection so they don't stay open forever.
            if order_id.startswith("PAPER-"):
                if not _is_market_open():
                    continue
                if not tp_val or not sl_val or not entry:
                    continue
                ticker_sym = trade.get("ticker", "")
                try:
                    async with session.get(
                        f"{_DATA_BASE}/v2/stocks/snapshots?symbols={ticker_sym}",
                        headers=_ALPACA_HEADERS,
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as rq:
                        if rq.status != 200:
                            continue
                        snap_data = await rq.json()
                except Exception:
                    continue

                snap  = (snap_data or {}).get(ticker_sym, {})
                lt    = snap.get("latestTrade") or {}
                db    = snap.get("dailyBar") or {}
                price = float(lt.get("p") or db.get("c") or 0)
                if price <= 0:
                    continue

                hit_tp = (direction == "LONG"  and price >= tp_val) or \
                         (direction == "SHORT" and price <= tp_val)
                hit_sl = (direction == "LONG"  and price <= sl_val) or \
                         (direction == "SHORT" and price >= sl_val)

                if not hit_tp and not hit_sl:
                    continue   # still open

                exit_price  = tp_val if hit_tp else sl_val
                exit_reason = "take_profit" if hit_tp else "stop_loss"
                if direction == "LONG":
                    pnl     = (exit_price - entry) * qty
                    pnl_pct = (exit_price - entry) / entry * 100
                else:
                    pnl     = (entry - exit_price) * qty
                    pnl_pct = (entry - exit_price) / entry * 100
                trade["status"]      = "closed"
                trade["exit"]        = round(exit_price, 2)
                trade["exit_reason"] = exit_reason
                trade["pnl"]         = round(pnl, 2)
                trade["pnl_pct"]     = round(pnl_pct, 2)
                trade["closed_at"]   = datetime.utcnow().isoformat()
                modified = True
                _update_agent_attribution(trade)
                logger.info(
                    "Closed (simulated) %s %s via %s: exit=%.2f  PnL=$%.2f (%.2f%%)",
                    direction, ticker_sym, exit_reason, exit_price, pnl, pnl_pct,
                )
                continue

            # ── Real Alpaca bracket order ──────────────────────────────────
            async with session.get(
                f"{_BROKER_BASE}/v2/orders/{order_id}",
                headers=_ALPACA_HEADERS,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status != 200:
                    continue
                order = await r.json()

            parent_status = order.get("status", "")
            legs = order.get("legs") or []
            exit_leg = next(
                (lg for lg in legs
                 if lg.get("status") == "filled" and lg.get("filled_avg_price")),
                None,
            )

            if exit_leg is not None:
                exit_price = float(exit_leg["filled_avg_price"])
                tp  = float(trade.get("take_profit") or (trade.get("risk") or {}).get("take_profit") or 0)
                sl  = float(trade.get("stop_loss")   or (trade.get("risk") or {}).get("stop_loss")   or 0)
                if tp and sl:
                    exit_reason = "take_profit" if abs(exit_price - tp) < abs(exit_price - sl) else "stop_loss"
                else:
                    exit_reason = "filled"

            elif parent_status in ("canceled", "expired", "done_for_day"):
                trade["status"]    = "cancelled"
                trade["closed_at"] = datetime.utcnow().isoformat()
                modified = True
                continue
            else:
                continue   # entry filled, exit pending

            direction = trade["direction"]
            entry     = float(trade["entry"])
            qty       = int(trade.get("qty", 1))

            if direction == "LONG":
                pnl     = (exit_price - entry) * qty
                pnl_pct = (exit_price - entry) / entry * 100
            else:
                pnl     = (entry - exit_price) * qty
                pnl_pct = (entry - exit_price) / entry * 100

            trade["status"]      = "closed"
            trade["exit"]        = round(exit_price, 2)
            trade["exit_reason"] = exit_reason
            trade["pnl"]         = round(pnl, 2)
            trade["pnl_pct"]     = round(pnl_pct, 2)
            trade["closed_at"]   = datetime.utcnow().isoformat()
            modified = True
            _update_agent_attribution(trade)
            logger.info("Closed %s %s via %s: exit=%.2f PnL=$%.2f (%.2f%%)",
                        direction, trade["ticker"], exit_reason, exit_price, pnl, pnl_pct)

        except Exception as exc:
            logger.debug("Could not check order %s: %s", trade.get("order_id"), exc)

    if modified:
        async with _trades_lock:
            _save(TRADES_FILE, trades)


# === Strategy weight learning ===

def _update_strategy_weights() -> None:
    trades  = _load(TRADES_FILE, [])
    weights = _load_weights()

    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None]
    recent = closed[-20:]
    if len(recent) < 15:
        return

    wins         = [t for t in recent if (t.get("pnl") or 0) > 0]
    long_trades  = [t for t in recent if t.get("direction") == "LONG"]
    short_trades = [t for t in recent if t.get("direction") == "SHORT"]
    long_wins    = [t for t in long_trades  if (t.get("pnl") or 0) > 0]
    short_wins   = [t for t in short_trades if (t.get("pnl") or 0) > 0]

    win_rate       = len(wins)       / len(recent)
    long_win_rate  = len(long_wins)  / len(long_trades)  if long_trades  else 0.5
    short_win_rate = len(short_wins) / len(short_trades) if short_trades else 0.5

    weights["win_rate_30d"]   = round(win_rate * 100, 1)
    weights["long_win_rate"]  = round(long_win_rate * 100, 1)
    weights["short_win_rate"] = round(short_win_rate * 100, 1)
    weights["update_count"]   = weights.get("update_count", 0) + 1
    weights["last_updated"]   = datetime.utcnow().isoformat()

    # NOTE: the self-tuner refines ATR/score params but does NOT activate live
    # tuning on its own — only a deliberate, OOS-validated optimizer Apply flips
    # live_tuning_active (avoids stepping live sizing from the DEFAULT baseline).
    # Once Apply has activated tuning, these refinements build on the applied values.
    if win_rate > 0.60:
        weights["min_score"]            = max(30,  weights["min_score"] - 1)
        weights["time_window_minutes"]   = min(60,  weights["time_window_minutes"] + 2)
        weights["atr_target_multiple"]   = min(5.0, weights["atr_target_multiple"] * 1.03)
        weights["chg_weight"]            = min(10.0, weights["chg_weight"] * 1.02)
    elif win_rate < 0.40:
        weights["min_score"]            = min(70,  weights["min_score"] + 2)
        weights["time_window_minutes"]   = max(20,  weights["time_window_minutes"] - 5)
        weights["atr_stop_multiple"]     = max(1.0, weights["atr_stop_multiple"] * 0.95)
        weights["chg_weight"]            = max(1.5, weights["chg_weight"] * 0.95)
        weights["intra_weight"]          = max(0.5, weights["intra_weight"] * 0.97)

    if long_win_rate > short_win_rate + 0.20:
        weights["bias"] = "long"
    elif short_win_rate > long_win_rate + 0.20:
        weights["bias"] = "short"
    else:
        weights["bias"] = "neutral"

    _save_weights(weights)


def _update_agent_attribution(trade: dict) -> None:
    """Update per-agent win/loss counters when a trade closes."""
    pnl = trade.get("pnl")
    if pnl is None:
        return
    evaluations = trade.get("evaluations") or []
    if not evaluations:
        return

    won = pnl > 0
    attr = _load(AGENT_PERF_FILE, {})
    for ev in evaluations:
        role = ev.get("role", "") if isinstance(ev, dict) else getattr(ev, "role", "")
        if not role:
            continue
        if role not in attr:
            attr[role] = {"wins": 0, "losses": 0, "total_pnl": 0.0}
        if won:
            attr[role]["wins"] += 1
        else:
            attr[role]["losses"] += 1
        attr[role]["total_pnl"] = round(attr[role]["total_pnl"] + float(pnl), 2)
    _save(AGENT_PERF_FILE, attr)


# === Signal revalidation ===

async def _revalidate_expired_recs(session: aiohttp.ClientSession) -> None:
    if not _is_market_open():
        # Don't reap recommendations while the market is closed — keep them
        # available until the next session re-scans.
        return

    recs = _load(RECS_FILE, [])
    if not recs:
        return

    now     = datetime.utcnow()
    expired = [r for r in recs if r.get("expires_at") and
               datetime.fromisoformat(r["expires_at"]) <= now]
    if not expired:
        return

    syms = list({r["ticker"] for r in expired})
    try:
        async with session.get(
            f"{_DATA_BASE}/v2/stocks/snapshots?symbols={','.join(syms)}",
            headers=_ALPACA_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                return
            snaps: Dict[str, Any] = await r.json()
    except Exception as exc:
        logger.warning("Revalidation snapshot fetch failed: %s", exc)
        return

    weights     = _load_weights()
    window_mins = weights.get("time_window_minutes", 45)
    kept = dropped = 0
    surviving = []

    for rec in recs:
        if rec not in expired:
            surviving.append(rec)
            continue

        ticker       = rec["ticker"]
        direction    = rec["direction"]
        entry        = float(rec["risk"]["entry"])
        reeval_count = rec.get("reeval_count", 0)

        snap    = snaps.get(ticker) or {}
        lt      = snap.get("latestTrade") or {}
        current = float(lt.get("p") or snap.get("dailyBar", {}).get("c") or entry)
        chg     = (current - entry) / entry * 100

        reversed_bad = chg < -1.0 if direction == "LONG" else chg > 1.0
        trend_holds  = chg >= -0.5 if direction == "LONG" else chg <= 0.5

        if reversed_bad or reeval_count >= 2:
            dropped += 1
            continue

        if trend_holds:
            rec["expires_at"]   = (now + timedelta(minutes=window_mins)).isoformat()
            rec["reeval_count"] = reeval_count + 1
            rec["reeval_note"]  = f"Extended (chg={chg:+.2f}%)"
            surviving.append(rec)
            kept += 1
        else:
            dropped += 1

    if kept or dropped:
        _save(RECS_FILE, surviving)
        logger.info("Revalidation: %d extended, %d dropped", kept, dropped)


# === Market scanner (Tasks #70 + #73) ===

_scan_counter: Dict[str, int] = {"n": 0}

_trades_lock: asyncio.Lock = None  # type: ignore[assignment]  # set in lifespan

_backtest_stats: Dict[str, Any] = {
    "last_run_at":   None,
    "last_status":   None,   # "ok" | "failed" | "timeout"
    "error_count":   0,
    "last_error":    None,
}

_optimizer_stats: Dict[str, Any] = {
    "last_run_at":   None,
    "last_status":   None,   # "ok" | "failed" | "timeout"
    "error_count":   0,
    "last_error":    None,
    "running":       False,
}

_challenge_stats: Dict[str, Any] = {
    "last_run_at":   None,
    "last_status":   None,
    "error_count":   0,
    "last_error":    None,
    "running":       False,
}

_circuit_breaker: Dict[str, Any] = {
    "halted":          False,
    "reason":          None,      # "daily_loss" | "consecutive_losses" | None
    "halted_at":       None,
    "consecutive_losses": 0,
    "daily_pnl_pct":   0.0,
}


def _reset_scan_stats_if_needed() -> None:
    today = str(date.today())
    if _scan_stats["date"] != today:
        _scan_stats.update({
            "date":             today,
            "scans_today":      0,
            "tickers_scanned":  0,
            "recs_generated":   0,
            "recs_skipped":     0,
            "scan_errors":      0,
            "last_scan_at":     None,
            "market_closed_skips": 0,
        })


def _daily_pnl_pct() -> float:
    """Today's realized P&L as fraction of current equity (negative = loss)."""
    today = str(date.today())
    trades = _load(TRADES_FILE, [])
    if not isinstance(trades, list):
        return 0.0
    today_trades = [
        t for t in trades
        if t.get("status") == "closed"
        and (t.get("closed_at") or "")[:10] == today
        and t.get("pnl") is not None
    ]
    if not today_trades:
        return 0.0
    total_pnl = sum(float(t["pnl"]) for t in today_trades)
    # Use yesterday's equity as baseline (we don't have real-time equity here)
    # Estimate from total account trades — minimum $1000 guard
    all_pnl = sum(float(t.get("pnl", 0)) for t in trades if t.get("status") == "closed")
    est_equity = max(abs(all_pnl) * 3 + 1000, 1000)  # conservative floor
    return total_pnl / est_equity


def _consecutive_losses() -> int:
    """Count of most recent consecutive losing closed trades."""
    trades = _load(TRADES_FILE, [])
    if not isinstance(trades, list):
        return 0
    closed = sorted(
        [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None],
        key=lambda t: t.get("closed_at") or t.get("executed_at") or "",
        reverse=True,
    )
    count = 0
    for t in closed:
        if float(t["pnl"]) < 0:
            count += 1
        else:
            break
    return count


def _check_circuit_breaker() -> Optional[str]:
    """Return a halt reason string if trading should be stopped, None if clear."""
    # Consecutive loss check
    consec = _consecutive_losses()
    _circuit_breaker["consecutive_losses"] = consec
    if consec >= MAX_CONSECUTIVE_LOSSES:
        reason = f"{consec} consecutive losses — trading halted until manual reset"
        _circuit_breaker.update({"halted": True, "reason": "consecutive_losses",
                                  "halted_at": datetime.utcnow().isoformat()})
        return reason

    # Daily P&L check
    daily_loss = _daily_pnl_pct()
    _circuit_breaker["daily_pnl_pct"] = round(daily_loss * 100, 2)
    if daily_loss <= -DAILY_LOSS_LIMIT_PCT:
        reason = f"Daily loss limit hit ({daily_loss*100:.1f}%) — trading halted for today"
        _circuit_breaker.update({"halted": True, "reason": "daily_loss",
                                  "halted_at": datetime.utcnow().isoformat()})
        return reason

    _circuit_breaker["halted"] = False
    _circuit_breaker["reason"] = None
    return None


async def _fetch_news_catalyst(session: aiohttp.ClientSession, sym: str) -> str:
    """Return the most recent Benzinga news headline for sym from last 24h, or ''."""
    try:
        since = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with session.get(
            f"{_DATA_BASE}/v1beta1/news?symbols={sym}&limit=1&start={since}",
            headers=_ALPACA_HEADERS,
            timeout=aiohttp.ClientTimeout(total=6),
        ) as r:
            if r.status != 200:
                return ""
            data = await r.json()
            articles = data.get("news", [])
            if articles:
                return articles[0].get("headline", "")
    except Exception:
        pass
    return ""


async def _run_premarket_scan() -> None:
    """Identify pre-market gappers (≥5% gap, price >$3, pre-market volume >50k) between 9:00-9:25 ET.

    Criteria sourced from Humbled Trader scanner: gap >5%, price >$3,
    pre-market vol >50k, Benzinga news catalyst preferred.

    Tags resulting recs with premarket=True and gap_pct. These appear in the
    dashboard with a PRE-MKT badge and give the trader a head start before the
    regular session opens at 9:30.
    """
    if not _ALPACA_KEY or not _ALPACA_SECRET:
        return

    gap_min = float(os.getenv("PREMARKET_GAP_MIN_PCT", "5.0"))
    vol_min = int(os.getenv("PREMARKET_MIN_VOLUME",    "50000"))

    try:
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
        ) as session:
            equity = await _get_account_equity(session)
            async with session.get(
                f"{_DATA_BASE}/v1beta1/screener/stocks/most-actives?by=volume&top=50",
                headers=_ALPACA_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return
                actives = await r.json()

            symbols = [
                item["symbol"]
                for item in actives.get("most_actives", [])
                if item.get("symbol") and "." not in item["symbol"]
            ][:50]

            if not symbols:
                return

            async with session.get(
                f"{_DATA_BASE}/v2/stocks/snapshots?symbols={','.join(symbols)}",
                headers=_ALPACA_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return
                snaps: Dict[str, Any] = await r.json()

        weights  = _load_weights()
        win_mins = weights.get("time_window_minutes", 45)
        recs: List[Dict[str, Any]] = []

        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
        ) as news_session:
            for sym, snap in snaps.items():
                try:
                    prev_bar   = snap.get("prevDailyBar") or {}
                    daily_bar  = snap.get("dailyBar") or {}
                    latest_trd = snap.get("latestTrade") or {}
                    prev_close = float(prev_bar.get("c") or 0)
                    price      = float(latest_trd.get("p") or daily_bar.get("o") or 0)

                    # Price filter: must be > $3
                    if prev_close <= 0 or price <= 0 or price < 3:
                        continue

                    gap_pct = (price - prev_close) / prev_close * 100
                    if abs(gap_pct) < gap_min:
                        continue

                    # Pre-market volume filter
                    pm_vol = float(daily_bar.get("v") or 0)
                    if pm_vol < vol_min:
                        continue

                    # News catalyst (non-blocking — empty string = no catalyst found)
                    catalyst = await _fetch_news_catalyst(news_session, sym)

                    direction   = "LONG" if gap_pct > 0 else "SHORT"
                    entry       = round(price, 2)
                    d           = 1 if direction == "LONG" else -1
                    stop_pct    = weights.get("stop_pct", 0.02)
                    tp_pct      = weights.get("tp_pct",   0.05)
                    stop_loss   = round(entry * (1 - d * stop_pct), 2)
                    take_profit = round(entry * (1 + d * tp_pct),  2)
                    qty         = _kelly_qty(equity, entry, stop_loss, take_profit, 65.0)
                    rr          = round(tp_pct / stop_pct, 2)
                    dollar_rsk  = round(abs(entry - stop_loss) * qty, 2)
                    expires_at  = (_next_market_open() + timedelta(minutes=win_mins)).isoformat()
                    rationale   = f"Pre-market gap {gap_pct:+.2f}% (prev close ${prev_close:.2f})"
                    if catalyst:
                        rationale += f" | {catalyst[:80]}"

                    recs.append({
                        "id":              f"{sym}-pm-{int(datetime.utcnow().timestamp())}",
                        "ticker":          sym,
                        "direction":       direction,
                        "composite_score": round(min(max(50.0 + abs(gap_pct) * 3, 55.0), 90.0), 1),
                        "agent_used":      False,
                        "rationale":       rationale,
                        "risk":            {"entry": entry, "stop_loss": stop_loss,
                                           "take_profit": take_profit, "qty": qty,
                                           "risk_reward": rr, "dollar_risk": dollar_rsk},
                        "regime":          "neutral",
                        "sector":          _SECTOR_MAP.get(sym, "Other"),
                        "scanned_at":      datetime.utcnow().isoformat(),
                        "expires_at":      expires_at,
                        "reeval_count":    0,
                        "hot_sector":      False,
                        "evaluations":     [],
                        "timestamp":       datetime.utcnow().isoformat(),
                        "chg_pct":         round(gap_pct, 2),
                        "premarket":       True,
                        "gap_pct":         round(gap_pct, 2),
                        "catalyst":        catalyst,
                    })
                except Exception:
                    continue

        if recs:
            recs.sort(key=lambda x: abs(x["gap_pct"]), reverse=True)
            existing = _load(RECS_FILE, [])
            if not isinstance(existing, list):
                existing = []
            non_pm = [r for r in existing if not r.get("premarket")]
            _save(RECS_FILE, recs + non_pm)
            logger.info(
                "Pre-market scan: %d gappers identified (>%.0f%% gap, vol>%d)",
                len(recs), gap_min, vol_min,
            )
            if _telegram is not None:
                asyncio.create_task(_telegram.send_gapper_alert(recs))

    except Exception as exc:
        logger.warning("Pre-market scan failed: %s", exc)


async def _run_market_scan() -> None:
    _reset_scan_stats_if_needed()

    if not _ALPACA_KEY or not _ALPACA_SECRET:
        logger.warning("Alpaca credentials missing -- skipping auto-scan")
        return

    if not _is_market_open():
        # Off-hours: throttle to one scan per hour. Each scan runs LLM agents
        # on ~20 symbols; every 5 min all night burns quota for stale signals.
        last = _scan_stats.get("last_scan_at")
        if last:
            try:
                age_min = (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds() / 60
                if age_min < 60:
                    return
            except Exception:
                pass
        _scan_stats["market_closed_skips"] += 1
        logger.info("Market closed — hourly off-hours scan (#%d today)", _scan_stats["market_closed_skips"])

    open_trades = [t for t in _load(TRADES_FILE, []) if t.get("status") == "open"]
    if len(open_trades) >= MAX_OPEN_POSITIONS:
        logger.info(
            "Max open positions (%d/%d) — skipping scan",
            len(open_trades), MAX_OPEN_POSITIONS,
        )
        return

    weights     = _load_weights()
    min_chg     = weights.get("min_chg_pct",        0.3)
    stop_pct    = weights.get("stop_pct",            0.02)
    tp_pct      = weights.get("tp_pct",              0.05)
    score_floor = weights.get("score_floor",         20)
    score_ceil  = weights.get("score_ceil",          80)
    min_score   = weights.get("min_score",           40)
    win_mins    = weights.get("time_window_minutes", 45)

    if _AGENTS_AVAILABLE and _pm is not None:
        _pm.risk.cfg.atr_stop_multiple   = weights.get("atr_stop_multiple",   2.0)
        _pm.risk.cfg.atr_target_multiple = weights.get("atr_target_multiple", 3.0)

    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())) as session:
            equity = await _get_account_equity(session)
            logger.info("Auto-scan: equity=$%.2f v%d agents=%s",
                        equity, weights.get("update_count", 0), _AGENTS_AVAILABLE)

            async with session.get(
                f"{_DATA_BASE}/v1beta1/screener/stocks/most-actives?by=volume&top=50",
                headers=_ALPACA_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return
                actives_data = await r.json()

            symbols_raw: List[str] = [
                item["symbol"]
                for item in actives_data.get("most_actives", [])
                if item.get("symbol") and "." not in item["symbol"]
            ][:50]

            if not symbols_raw:
                return

            async with session.get(
                f"{_DATA_BASE}/v2/stocks/snapshots?symbols={','.join(symbols_raw)}",
                headers=_ALPACA_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return
                snaps: Dict[str, Any] = await r.json()

            bars_map = await _fetch_multi_bars(session, list(set(symbols_raw + ["SPY", "QQQ", "VIXY"])), limit=100)
            vix_index = await _fetch_vix_index(session)

            # Fetch 1-hour bars for multi-timeframe confirmation gate
            hourly_map = await _fetch_multi_bars(session, symbols_raw, timeframe="1Hour", limit=60)
            earnings_blacklist = await _fetch_earnings_blacklist(session)

        # Compute beta (correlation to SPY) for each ticker
        ticker_beta: Dict[str, float] = {}
        spy_df = bars_map.get("SPY")
        if _AGENTS_AVAILABLE and spy_df is not None and len(spy_df) >= 20:
            spy_ret = spy_df["close"].pct_change().dropna()
            for _sym in symbols_raw:
                _df = bars_map.get(_sym)
                if _df is None or len(_df) < 20:
                    ticker_beta[_sym] = 1.0
                    continue
                sym_ret = _df["close"].pct_change().dropna()
                aligned = pd.concat([sym_ret, spy_ret], axis=1, join="inner")
                if len(aligned) < 10:
                    ticker_beta[_sym] = 1.0
                    continue
                cov_ = float(aligned.iloc[:, 0].cov(aligned.iloc[:, 1]))
                var_ = float(aligned.iloc[:, 1].var())
                ticker_beta[_sym] = round(cov_ / var_, 2) if var_ > 0 else 1.0

        if _AGENTS_AVAILABLE and _pm is not None:
            _pm.technical.spy_bars = bars_map.get("SPY")

        # Save regime snapshot (vix/spy/qqq daily change)
        def _daily_chg(df: Any) -> float:
            if df is None or len(df) < 2:
                return 0.0
            try:
                prev_close = float(df.iloc[-2]["close"])
                last_close = float(df.iloc[-1]["close"])
                return round((last_close - prev_close) / prev_close * 100, 2) if prev_close else 0.0
            except Exception:
                return 0.0

        spy_chg  = _daily_chg(bars_map.get("SPY"))
        qqq_chg  = _daily_chg(bars_map.get("QQQ"))
        vixy_df  = bars_map.get("VIXY")
        vix_approx = 0.0
        if vixy_df is not None and len(vixy_df) > 0:
            try:
                vix_approx = round(float(vixy_df.iloc[-1]["close"]), 1)
            except Exception:
                vix_approx = 0.0

        # Prefer the real CBOE VIX index; fall back to the VIXY ETF price
        # (a related but differently-scaled proxy) if Yahoo is unreachable.
        if vix_index > 0:
            vix_level = vix_index
            vix_label = "VIX"
        elif vix_approx > 0:
            vix_level = vix_approx
            vix_label = "VIX-proxy"
        else:
            vix_level = 15.0
            vix_label = "VIX"

        if spy_chg > 0.5 and qqq_chg > 0.5 and vix_level < 25:
            regime_label = "risk_on"
            regime_rationale = f"SPY +{spy_chg:.2f}%, QQQ +{qqq_chg:.2f}%, {vix_label} {vix_level:.1f} — bullish"
        elif spy_chg < -0.5 or vix_level > 35:
            regime_label = "risk_off"
            regime_rationale = f"SPY {spy_chg:.2f}%, {vix_label} {vix_level:.1f} — bearish"
        elif abs(spy_chg) < 0.3 and abs(qqq_chg) < 0.3:
            regime_label = "choppy"
            regime_rationale = f"SPY {spy_chg:.2f}%, QQQ {qqq_chg:.2f}% — low momentum"
        else:
            regime_label = "neutral"
            regime_rationale = f"SPY {spy_chg:.2f}%, QQQ {qqq_chg:.2f}%"

        _save(REGIME_FILE, {
            "regime":      regime_label,
            "vix_level":   vix_level,
            "spy_day_chg": spy_chg,
            "qqq_day_chg": qqq_chg,
            "rationale":   regime_rationale,
            "timestamp":   datetime.utcnow().isoformat(),
            "reasoning": {
                "regime": regime_label,
                "rationale": regime_rationale,
                "inputs": {
                    "vix": vix_level,
                    "vix_label": vix_label,
                    "spy_day_chg_pct": spy_chg,
                    "qqq_day_chg_pct": qqq_chg,
                },
                "rules": {
                    "risk_on":  "SPY and QQQ both up > 0.5% intraday and VIX < 25",
                    "risk_off": "SPY down > 0.5% intraday or VIX > 35",
                    "choppy":   "SPY and QQQ both within ±0.3% intraday",
                    "neutral":  "All other conditions",
                },
            },
        })

        recs: List[Dict[str, Any]] = []

        for sym in symbols_raw:
            snap = snaps.get(sym) or {}
            if not snap:
                continue

            if sym in earnings_blacklist:
                _scan_stats["recs_skipped"] += 1
                continue

            daily_bar  = snap.get("dailyBar")     or {}
            prev_bar   = snap.get("prevDailyBar") or {}
            latest_trd = snap.get("latestTrade")  or {}

            price      = float(latest_trd.get("p") or daily_bar.get("c") or 0)
            prev_close = float(prev_bar.get("c") or price)
            day_open   = float(daily_bar.get("o") or price)

            if price < 5 or price > 2000:
                continue

            chg_pct   = (price - prev_close) / prev_close * 100 if prev_close else 0
            intra_pct = (price - day_open)   / day_open   * 100 if day_open   else 0

            if abs(chg_pct) < min_chg:
                continue

            df         = bars_map.get(sym)
            agent_used = False
            rationale  = ""
            evaluations_out = []

            if _AGENTS_AVAILABLE and df is not None and len(df) >= 20:
                ctx      = AnalysisContext(ticker=sym, bars=df, account={"equity": equity},
                                          hourly_bars=hourly_map.get(sym))
                decision = await _evaluate(ctx)
                if decision is None:
                    continue
                score       = decision.composite_score
                agent_evals = decision.evaluations
                evaluations_out   = [
                    {
                        "role":       ev.role.value if hasattr(ev.role, "value") else str(ev.role),
                        "score":      round(float(ev.score), 1),
                        "confidence": round(float(ev.confidence), 2),
                        "rationale":  ev.rationale or "",
                        "reasoning":  ev.reasoning,
                    }
                    for ev in agent_evals
                ]
                # Primary rationale from technical agent if available
                for ev in agent_evals:
                    if hasattr(ev.role, "value") and ev.role.value == "technical":
                        rationale = ev.rationale or ""
                        break

                # Dashboard "ideas" gate is looser (55/45 + self-tuned
                # min_score) than the bot's trade gate — keep it that way.
                if score > 55:
                    direction = "LONG"
                elif score < 45:
                    direction = "SHORT"
                else:
                    continue

                if score < min_score:
                    continue

                agent_used = True
                intended   = _Decision.LONG if direction == "LONG" else _Decision.SHORT
                # Reuse the plan pm.decide() already built when it agrees with
                # the dashboard direction; otherwise build one for this side.
                if decision.is_actionable and decision.decision.value == direction:
                    plan = decision.risk
                else:
                    plan = _pm.risk.build_plan(ctx, intended=intended)

                if plan is not None and plan.risk_reward >= 1.0:
                    entry       = round(plan.entry, 2)
                    stop_loss   = round(plan.stop_loss, 2)
                    take_profit = round(plan.take_profit, 2)
                    qty         = int(plan.qty)   # 0 when unsizable — never fabricate
                    rr          = round(plan.risk_reward, 2)
                else:
                    entry = round(price, 2)
                    d     = 1 if direction == "LONG" else -1
                    stop_loss   = round(entry * (1 - d * stop_pct), 2)
                    take_profit = round(entry * (1 + d * tp_pct),   2)
                    qty  = _kelly_qty(equity, entry, stop_loss, take_profit, score)
                    rr   = round(tp_pct / stop_pct, 2)

            else:
                chg_w   = weights.get("chg_weight", 4.0)
                intra_w = weights.get("intra_weight", 2.0)
                score   = min(max(50 + chg_pct * chg_w + intra_pct * intra_w, score_floor), score_ceil)
                if score < min_score:
                    continue
                direction   = "LONG" if chg_pct > 0 else "SHORT"
                entry       = round(price, 2)
                d           = 1 if direction == "LONG" else -1
                stop_loss   = round(entry * (1 - d * stop_pct), 2)
                take_profit = round(entry * (1 + d * tp_pct),   2)
                qty         = _kelly_qty(equity, entry, stop_loss, take_profit, score)
                rr          = round(tp_pct / stop_pct, 2)
                rationale   = f"fallback chg={chg_pct:+.1f}% intra={intra_pct:+.1f}%"

            dollar_rsk  = round(abs(entry - stop_loss) * qty, 2)
            # Off-hours recs stay valid until the next market open (+ the usual
            # window) so they're still fresh for Monday instead of showing as
            # "expired" all weekend.
            expires_base = datetime.utcnow() if _is_market_open() else _next_market_open()
            expires_iso  = (expires_base + timedelta(minutes=win_mins)).isoformat()

            recs.append({
                "id":              f"{sym}-{int(datetime.utcnow().timestamp())}",
                "ticker":          sym,
                "direction":       direction,
                "composite_score": round(score, 1),
                "agent_used":      agent_used,
                "rationale":       rationale,
                "risk": {
                    "entry":       entry,
                    "stop_loss":   stop_loss,
                    "take_profit": take_profit,
                    "qty":         qty,
                    "risk_reward": rr,
                    "dollar_risk": dollar_rsk,
                },
                "regime":       "neutral",
                "sector":       _SECTOR_MAP.get(sym, "Other"),
                "scanned_at":   datetime.utcnow().isoformat(),
                "expires_at":   expires_iso,
                "reeval_count": 0,
                "hot_sector":   False,
                "evaluations":  evaluations_out,
                "timestamp":    datetime.utcnow().isoformat(),
                "chg_pct":      round(chg_pct, 2),
                "beta":         ticker_beta.get(sym, 1.0),
            })

        # Mark hot_sector = True for recs in the top-2 scoring sectors
        if recs:
            from collections import defaultdict as _dd
            sector_scores: dict = _dd(list)
            for r in recs:
                sector_scores[r["sector"]].append(r["composite_score"])
            sector_avg = {s: sum(v)/len(v) for s, v in sector_scores.items()}
            top_sectors = set(sorted(sector_avg, key=sector_avg.get, reverse=True)[:2])
            for r in recs:
                r["hot_sector"] = r["sector"] in top_sectors

        recs.sort(key=lambda x: x["composite_score"], reverse=True)
        if recs or _is_market_open():
            _save(RECS_FILE, recs)
        # else: market closed and this scan found nothing — keep whatever
        # recommendations are already on disk available until Monday.

        # Push high-conviction signals to Telegram
        if _telegram is not None and recs:
            strong_hits = [r for r in recs if r["composite_score"] > 60]
            if strong_hits:
                asyncio.create_task(_telegram.send_strategy_alert(strong_hits))

        scanned_n = len(symbols_raw)
        skipped_n = scanned_n - len(recs)
        _scan_stats["scans_today"]     += 1
        _scan_stats["tickers_scanned"] += scanned_n
        _scan_stats["recs_generated"]  += len(recs)
        _scan_stats["recs_skipped"]    += skipped_n
        _scan_stats["last_scan_at"]     = datetime.utcnow().isoformat()
        logger.info(
            "Scan complete: %d recs from %d symbols — skipped=%d agents=%s",
            len(recs), scanned_n, skipped_n, _AGENTS_AVAILABLE,
        )

        _scan_counter["n"] += 1
        if _scan_counter["n"] % 3 == 0:
            _update_strategy_weights()

    except Exception as exc:
        _scan_stats["scan_errors"] += 1
        logger.exception("Market scan failed: %s", exc)


# === Background loop ===

_BACKTEST_SCRIPT  = _HERE / "backtest_30day.py"
_RESULTS_FILE     = _HERE.parent / "backtest_results.json"
_BACKTEST_INTERVAL_H = int(os.getenv("BACKTEST_INTERVAL_H", "24"))


async def _run_backtest() -> None:
    """Run backtest_30day.py as a subprocess (non-blocking)."""
    if not _BACKTEST_SCRIPT.exists():
        logger.warning("backtest_30day.py not found — skipping auto-backtest")
        return
    logger.info("Auto-backtest starting (interval=%dh)…", _BACKTEST_INTERVAL_H)
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(_BACKTEST_SCRIPT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_HERE),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1800)  # 30 min max
        if proc.returncode == 0:
            logger.info("Auto-backtest complete — results written to %s", _RESULTS_FILE)
            _backtest_stats.update({
                "last_run_at": datetime.utcnow().isoformat(),
                "last_status": "ok",
                "last_error":  None,
            })
        else:
            decoded_tail = (stdout or b"").decode()[-500:]
            logger.error("Auto-backtest failed (rc=%d): %s", proc.returncode, decoded_tail)
            _backtest_stats.update({
                "last_run_at": datetime.utcnow().isoformat(),
                "last_status": "failed",
                "error_count": _backtest_stats["error_count"] + 1,
                "last_error":  decoded_tail,
            })
    except asyncio.TimeoutError:
        logger.error("Auto-backtest timed out after 30 min — killed")
        if proc is not None:
            proc.kill()
        _backtest_stats.update({
            "last_run_at": datetime.utcnow().isoformat(),
            "last_status": "timeout",
            "error_count": _backtest_stats["error_count"] + 1,
            "last_error":  "timed out after 30 min",
        })
    except Exception:
        logger.exception("Auto-backtest subprocess error")
        _backtest_stats.update({
            "last_run_at": datetime.utcnow().isoformat(),
            "last_status": "failed",
            "error_count": _backtest_stats["error_count"] + 1,
        })


_OPTIMIZER_SCRIPT = _HERE / "optimize_strategy.py"


async def _run_optimizer() -> None:
    """Run optimize_strategy.py as a subprocess (non-blocking).

    Writes backtest_optimal.json + OPTIMAL_CONFIG.txt, which the dashboard's
    /backtest 'Optimizer Run' panel and config box read. Guarded so two runs
    can't overlap (the job is heavy: grid search × walk-forward × full agents).
    """
    if not _OPTIMIZER_SCRIPT.exists():
        logger.warning("optimize_strategy.py not found — skipping optimizer")
        return
    if _optimizer_stats.get("running"):
        logger.info("Optimizer already running — ignoring trigger")
        return
    _optimizer_stats["running"] = True
    logger.info("Optimizer starting…")
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(_OPTIMIZER_SCRIPT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_HERE),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3000)  # 50 min max
        if proc.returncode == 0:
            logger.info("Optimizer complete — wrote backtest_optimal.json + OPTIMAL_CONFIG.txt")
            _optimizer_stats.update({
                "last_run_at": datetime.utcnow().isoformat(),
                "last_status": "ok",
                "last_error":  None,
            })
        else:
            decoded_tail = (stdout or b"").decode()[-500:]
            logger.error("Optimizer failed (rc=%d): %s", proc.returncode, decoded_tail)
            _optimizer_stats.update({
                "last_run_at": datetime.utcnow().isoformat(),
                "last_status": "failed",
                "error_count": _optimizer_stats["error_count"] + 1,
                "last_error":  decoded_tail,
            })
    except asyncio.TimeoutError:
        logger.error("Optimizer timed out after 50 min — killed")
        if proc is not None:
            proc.kill()
        _optimizer_stats.update({
            "last_run_at": datetime.utcnow().isoformat(),
            "last_status": "timeout",
            "error_count": _optimizer_stats["error_count"] + 1,
            "last_error":  "timed out after 50 min",
        })
    except Exception:
        logger.exception("Optimizer subprocess error")
        _optimizer_stats.update({
            "last_run_at": datetime.utcnow().isoformat(),
            "last_status": "failed",
            "error_count": _optimizer_stats["error_count"] + 1,
        })
    finally:
        _optimizer_stats["running"] = False


_CHALLENGE_SCRIPT = _HERE / "challenge_runner.py"


async def _run_challenge(mode: str = "run") -> None:
    """Run challenge_runner.py as a subprocess. mode: 'run' | 'list' | 'status'.

    Writes challenge_results.json (read by the dashboard). Needs AI4Trade creds;
    without them the script exits cleanly and records an auth error.
    """
    if not _CHALLENGE_SCRIPT.exists():
        logger.warning("challenge_runner.py not found — skipping")
        return
    if _challenge_stats.get("running"):
        logger.info("Challenge runner already running — ignoring trigger")
        return
    _challenge_stats["running"] = True
    args = [sys.executable, str(_CHALLENGE_SCRIPT)]
    if mode == "list":
        args.append("--list")
    elif mode == "status":
        args.append("--status")
    logger.info("Challenge runner starting (mode=%s)…", mode)
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_HERE),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)  # 10 min max
        if proc.returncode == 0:
            _challenge_stats.update({"last_run_at": datetime.utcnow().isoformat(),
                                     "last_status": "ok", "last_error": None})
        else:
            tail = (stdout or b"").decode()[-500:]
            logger.error("Challenge runner failed (rc=%d): %s", proc.returncode, tail)
            _challenge_stats.update({"last_run_at": datetime.utcnow().isoformat(),
                                     "last_status": "failed",
                                     "error_count": _challenge_stats["error_count"] + 1,
                                     "last_error": tail})
    except asyncio.TimeoutError:
        logger.error("Challenge runner timed out — killed")
        if proc is not None:
            proc.kill()
        _challenge_stats.update({"last_run_at": datetime.utcnow().isoformat(),
                                 "last_status": "timeout",
                                 "error_count": _challenge_stats["error_count"] + 1,
                                 "last_error": "timed out after 10 min"})
    except Exception:
        logger.exception("Challenge runner subprocess error")
        _challenge_stats.update({"last_run_at": datetime.utcnow().isoformat(),
                                 "last_status": "failed",
                                 "error_count": _challenge_stats["error_count"] + 1})
    finally:
        _challenge_stats["running"] = False


async def _eod_snapshot(session: aiohttp.ClientSession) -> None:
    """Record daily P&L vs SPY/QQQ benchmark at ~15:55 ET."""
    try:
        today = str(date.today())

        # Fetch account equity
        equity_now = await _get_account_equity(session)

        # Fetch SPY and QQQ latest quotes for day return
        async def _day_return(sym: str) -> Optional[float]:
            try:
                async with session.get(
                    f"{_DATA_BASE}/v2/stocks/{sym}/bars",
                    params={"timeframe": "1Day", "limit": "2", "feed": "iex"},
                    headers=_ALPACA_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as r:
                    if r.status != 200:
                        return None
                    data = await r.json()
                    bars = data.get("bars", [])
                    if len(bars) < 2:
                        return None
                    prev_close = float(bars[-2].get("c", 0))
                    last_close = float(bars[-1].get("c", 0))
                    if prev_close <= 0:
                        return None
                    return round((last_close - prev_close) / prev_close * 100, 3)
            except Exception:
                return None

        spy_ret, qqq_ret = await asyncio.gather(_day_return("SPY"), _day_return("QQQ"))

        # Compute today's trade P&L
        trades = _load(TRADES_FILE, [])
        today_closed = [
            t for t in trades
            if t.get("status") == "closed" and (t.get("closed_at") or "")[:10] == today
        ]
        day_pnl = sum(float(t.get("pnl", 0)) for t in today_closed)
        open_count = len([t for t in trades if t.get("status") == "open"])

        snapshot = {
            "date":              today,
            "equity":            round(equity_now, 2) if equity_now > 0 else None,
            "day_pnl":           round(day_pnl, 2),
            "day_trades":        len(today_closed),
            "open_positions":    open_count,
            "spy_day_return_pct": spy_ret,
            "qqq_day_return_pct": qqq_ret,
        }

        with open(SNAPSHOT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot) + "\n")
        logger.info("EoD snapshot: pnl=$%.2f SPY=%.2f%% QQQ=%.2f%%",
                    day_pnl, spy_ret or 0.0, qqq_ret or 0.0)

    except Exception as exc:
        logger.warning("EoD snapshot failed: %s", exc)


async def _trailing_stop_loop() -> None:
    """Update stop prices upward (LONG) or downward (SHORT) as positions move in our favor.

    Runs every 60 seconds during market hours. Never moves stop in the losing direction.
    Closes position immediately when trailing stop is hit (for PAPER- simulated orders).
    """
    if not _ALPACA_KEY or not _ALPACA_SECRET:
        return

    while True:
        await asyncio.sleep(60)
        if not _is_market_open():
            continue

        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
            ) as session:
                trades = _load(TRADES_FILE, [])
                open_trades = [t for t in trades if t.get("status") == "open"]
                if not open_trades:
                    continue

                # Fetch current prices for all open tickers
                tickers = list({t["ticker"] for t in open_trades})
                try:
                    async with session.get(
                        f"{_DATA_BASE}/v2/stocks/snapshots?symbols={','.join(tickers)}",
                        headers=_ALPACA_HEADERS,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        if r.status != 200:
                            continue
                        snaps: Dict[str, Any] = await r.json()
                except Exception:
                    continue

                modified = False
                for trade in open_trades:
                    ticker    = trade.get("ticker", "")
                    direction = trade.get("direction", "LONG")
                    snap      = snaps.get(ticker, {})
                    lt        = snap.get("latestTrade") or {}
                    db        = snap.get("dailyBar") or {}
                    price     = float(lt.get("p") or db.get("c") or 0)
                    if price <= 0:
                        continue

                    risk = trade.get("risk") or trade
                    stop = float(risk.get("stop_loss") or trade.get("stop_loss") or 0)
                    if stop <= 0:
                        continue

                    if direction == "LONG":
                        # Ratchet stop UP: new_stop = price × (1 - TRAIL_PCT)
                        new_stop = round(price * (1.0 - TRAIL_STOP_PCT), 2)
                        if new_stop > stop:
                            if "risk" in trade and isinstance(trade["risk"], dict):
                                trade["risk"]["stop_loss"] = new_stop
                                trade["risk"]["high_water_mark"] = max(
                                    price, trade["risk"].get("high_water_mark", price)
                                )
                            trade["stop_loss"] = new_stop
                            modified = True
                            logger.debug("Trail stop updated %s LONG stop %.2f -> %.2f",
                                         ticker, stop, new_stop)
                        # Check if current price hit the stop
                        effective_stop = max(stop, new_stop) if new_stop > stop else stop
                        if price <= effective_stop:
                            _close_simulated_trade(trade, effective_stop, "trailing_stop")
                            modified = True
                            logger.info("Trailing stop hit: %s LONG closed @ %.2f", ticker, effective_stop)

                    else:  # SHORT
                        # Ratchet stop DOWN: new_stop = price × (1 + TRAIL_PCT)
                        new_stop = round(price * (1.0 + TRAIL_STOP_PCT), 2)
                        if new_stop < stop:
                            if "risk" in trade and isinstance(trade["risk"], dict):
                                trade["risk"]["stop_loss"] = new_stop
                                trade["risk"]["low_water_mark"] = min(
                                    price, trade["risk"].get("low_water_mark", price)
                                )
                            trade["stop_loss"] = new_stop
                            modified = True
                            logger.debug("Trail stop updated %s SHORT stop %.2f -> %.2f",
                                         ticker, stop, new_stop)
                        effective_stop = min(stop, new_stop) if new_stop < stop else stop
                        if price >= effective_stop:
                            _close_simulated_trade(trade, effective_stop, "trailing_stop")
                            modified = True
                            logger.info("Trailing stop hit: %s SHORT closed @ %.2f", ticker, effective_stop)

                if modified:
                    async with _trades_lock:
                        _save(TRADES_FILE, trades)

        except Exception as exc:
            logger.warning("Trailing stop loop error: %s", exc)


# ---------------------------------------------------------------------------
# Position exit monitor helpers
# ---------------------------------------------------------------------------

async def _close_position_via_alpaca(session: aiohttp.ClientSession, ticker: str) -> bool:
    """Close a single position via Alpaca DELETE /v2/positions/{symbol}."""
    try:
        async with session.delete(
            f"{_BROKER_BASE}/v2/positions/{ticker}",
            headers=_ALPACA_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status not in (200, 204):
                logger.warning("Alpaca close %s -> HTTP %s: %s", ticker, r.status, await r.text())
                return False
            return True
    except Exception as exc:
        logger.warning("Alpaca close %s exception: %s", ticker, exc)
        return False


async def _fetch_bars_for_exit(session: aiohttp.ClientSession, ticker: str):
    """Fetch recent 5-min bars for a ticker. Returns DataFrame or None."""
    try:
        async with session.get(
            f"{_DATA_BASE}/v2/stocks/{ticker}/bars?timeframe=5Min&limit=100&feed=iex",
            headers=_ALPACA_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                return None
            raw = await r.json()
    except Exception:
        return None

    bars_data = raw.get("bars", [])
    if len(bars_data) < 20:
        return None
    bars = pd.DataFrame(bars_data)
    bars.rename(columns={"o": "open", "h": "high", "l": "low",
                         "c": "close", "v": "volume", "t": "timestamp"}, inplace=True)
    bars["timestamp"] = pd.to_datetime(bars["timestamp"])
    bars.set_index("timestamp", inplace=True)
    return bars


def _log_exit_decision(ticker: str, direction: str, action: str,
                       reason: str, score: Optional[float] = None,
                       price: Optional[float] = None) -> None:
    entry: Dict[str, Any] = {
        "ts":        datetime.utcnow().isoformat(),
        "ticker":    ticker,
        "direction": direction,
        "action":    action,   # "exit" | "hold" | "hold_overnight"
        "reason":    reason,
    }
    if score  is not None: entry["score"] = round(score, 1)
    if price  is not None: entry["price"] = round(price, 2)
    _EXIT_DECISIONS.append(entry)
    if len(_EXIT_DECISIONS) > _MAX_EXIT_LOG:
        _EXIT_DECISIONS[:] = _EXIT_DECISIONS[-_MAX_EXIT_LOG:]


async def _do_exit_position(trade: dict, price: float, reason: str,
                            session: aiohttp.ClientSession,
                            score: Optional[float] = None) -> bool:
    """Close a position (simulated PAPER or real Alpaca) and record decision."""
    ticker    = trade.get("ticker", "")
    direction = trade.get("direction", "LONG")
    order_id  = trade.get("order_id", "")

    if order_id.startswith("PAPER-") or not order_id:
        _close_simulated_trade(trade, price, reason)
    else:
        ok = await _close_position_via_alpaca(session, ticker)
        if not ok:
            logger.warning("Exit monitor: could not close %s via Alpaca", ticker)
            return False
        _close_simulated_trade(trade, price, reason)

    _log_exit_decision(ticker, direction, "exit", reason, score=score, price=price)
    logger.info("Position EXIT: %s %s @ %.2f — %s", direction, ticker, price, reason)
    return True


# ---------------------------------------------------------------------------
# Continuous position exit monitor (every EXIT_MONITOR_INTERVAL_MIN minutes)
# ---------------------------------------------------------------------------

async def _position_exit_monitor_loop() -> None:
    """Re-score open positions on a cadence. Exit any whose signal has flipped.

    Uses TechnicalAgent only for speed (no LLM cost per position per cycle).
    Confidence gate (≥0.50) prevents noise-driven exits on thin signals.
    """
    while True:
        await asyncio.sleep(EXIT_MONITOR_INTERVAL_MIN * 60)

        if not _is_market_open() or not _AGENTS_AVAILABLE or _pm is None:
            continue

        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
            ) as session:
                trades = _load(TRADES_FILE, [])
                open_trades = [t for t in trades if t.get("status") == "open"]
                if not open_trades:
                    continue

                # Batch-fetch current prices
                tickers = list({t["ticker"] for t in open_trades})
                try:
                    async with session.get(
                        f"{_DATA_BASE}/v2/stocks/snapshots?symbols={','.join(tickers)}",
                        headers=_ALPACA_HEADERS,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        snaps: Dict[str, Any] = await r.json() if r.status == 200 else {}
                except Exception:
                    snaps = {}

                modified = False
                for trade in open_trades:
                    ticker    = trade.get("ticker", "")
                    direction = trade.get("direction", "LONG")
                    snap      = snaps.get(ticker, {})
                    price     = float(
                        (snap.get("latestTrade") or {}).get("p") or
                        (snap.get("dailyBar")    or {}).get("c") or 0
                    )

                    bars = await _fetch_bars_for_exit(session, ticker)
                    if bars is None:
                        continue

                    try:
                        ctx = AnalysisContext(ticker=ticker, bars=bars)
                        ev  = await asyncio.wait_for(_pm.technical.evaluate(ctx), timeout=10.0)
                    except Exception:
                        continue

                    score      = ev.score
                    confidence = ev.confidence
                    exit_threshold_long  = EXIT_SCORE_THRESHOLD
                    exit_threshold_short = 100.0 - EXIT_SCORE_THRESHOLD

                    should_exit = (
                        (direction == "LONG"  and score < exit_threshold_long  and confidence >= 0.50) or
                        (direction == "SHORT" and score > exit_threshold_short and confidence >= 0.50)
                    )

                    if should_exit:
                        reason = (
                            f"exit_monitor: technical score {score:.0f} "
                            f"({'< ' + str(exit_threshold_long) if direction == 'LONG' else '> ' + str(exit_threshold_short)}) "
                            f"flipped against {direction} (conf {confidence:.0%}) — {ev.rationale}"
                        )
                        p = price if price > 0 else float(trade.get("entry", 0))
                        ok = await _do_exit_position(trade, p, reason, session, score=score)
                        if ok:
                            modified = True
                    else:
                        _log_exit_decision(
                            ticker, direction, "hold",
                            f"exit_monitor: score {score:.0f} still supports {direction} (conf {confidence:.0%})",
                            score=score, price=price,
                        )

                if modified:
                    async with _trades_lock:
                        _save(TRADES_FILE, trades)

        except Exception as exc:
            logger.warning("Position exit monitor error: %s", exc)


# ---------------------------------------------------------------------------
# EOD position review (fires EOD_REVIEW_MIN_BEFORE minutes before 16:00 ET)
# ---------------------------------------------------------------------------

async def _eod_position_review_loop() -> None:
    """Intelligent end-of-day position review.

    Fires once per trading day EOD_REVIEW_MIN_BEFORE minutes before the
    16:00 ET close. Runs the full agent evaluation on every open position
    and decides to hold overnight (if ALLOW_OVERNIGHT=true and agents still
    support the trade) or close.

    The existing EOD_FLATTEN in bootstrap (15:55 by default) acts as a hard
    safety net after this review has already made intelligent decisions.
    """
    last_review_day = ""

    while True:
        await asyncio.sleep(60)
        if _ET is None:
            continue

        now_et = datetime.now(_ET)
        today  = str(date.today())

        # Target: EOD_REVIEW_MIN_BEFORE minutes before 16:00 ET
        review_minutes_from_midnight = 16 * 60 - EOD_REVIEW_MIN_BEFORE
        target_hour   = review_minutes_from_midnight // 60
        target_minute = review_minutes_from_midnight % 60

        if (today == last_review_day
                or now_et.weekday() >= 5
                or now_et.hour != target_hour
                or now_et.minute < target_minute):
            continue

        last_review_day = today
        logger.info("EOD position review: %d min before close", EOD_REVIEW_MIN_BEFORE)

        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
            ) as session:
                trades = _load(TRADES_FILE, [])
                open_trades = [t for t in trades if t.get("status") == "open"]
                if not open_trades:
                    logger.info("EOD review: no open positions")
                    continue

                tickers = list({t["ticker"] for t in open_trades})
                try:
                    async with session.get(
                        f"{_DATA_BASE}/v2/stocks/snapshots?symbols={','.join(tickers)}",
                        headers=_ALPACA_HEADERS,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        snaps: Dict[str, Any] = await r.json() if r.status == 200 else {}
                except Exception:
                    snaps = {}

                modified = False
                kept: list = []
                closed: list = []

                for trade in open_trades:
                    ticker    = trade.get("ticker", "")
                    direction = trade.get("direction", "LONG")
                    snap      = snaps.get(ticker, {})
                    price     = float(
                        (snap.get("latestTrade") or {}).get("p") or
                        (snap.get("dailyBar")    or {}).get("c") or 0
                    )
                    p = price if price > 0 else float(trade.get("entry", 0))

                    # Full agent re-evaluation (same pipeline as entry scan)
                    score:  Optional[float] = None
                    decision = None
                    bars = await _fetch_bars_for_exit(session, ticker)
                    if bars is not None and _AGENTS_AVAILABLE and _pm is not None:
                        try:
                            ctx      = AnalysisContext(ticker=ticker, bars=bars)
                            decision = await _evaluate(ctx)
                            if decision is not None:
                                score = getattr(decision, "composite_score", None)
                        except Exception as exc:
                            logger.warning("EOD review eval failed for %s: %s", ticker, exc)

                    # Decide: close vs keep overnight
                    should_exit = True
                    reason: str

                    if ALLOW_OVERNIGHT and score is not None:
                        still_bullish = direction == "LONG"  and score >= 55
                        still_bearish = direction == "SHORT" and score <= 45
                        if still_bullish or still_bearish:
                            should_exit = False
                            reason = (
                                f"eod_review: HELD overnight — "
                                f"agents still support {direction} (score {score:.0f})"
                            )
                        else:
                            reason = (
                                f"eod_review: CLOSED — score {score:.0f} no longer supports "
                                f"{direction} (overnight allowed but signal faded)"
                            )
                    elif score is not None:
                        reason = (
                            f"eod_review: CLOSED — day-trade mode "
                            f"(score {score:.0f}, ALLOW_OVERNIGHT=false)"
                        )
                    else:
                        reason = "eod_review: CLOSED — could not re-evaluate, closing for safety"

                    if should_exit:
                        ok = await _do_exit_position(trade, p, reason, session, score=score)
                        if ok:
                            closed.append(ticker)
                            modified = True
                    else:
                        _log_exit_decision(ticker, direction, "hold_overnight", reason, score=score, price=p)
                        kept.append(ticker)
                        logger.info("EOD review: KEPT %s %s overnight (score %.0f)", direction, ticker, score or 0)

                if modified:
                    async with _trades_lock:
                        _save(TRADES_FILE, trades)

                logger.info(
                    "EOD review done — closed: %s | kept overnight: %s",
                    closed or "none", kept or "none",
                )

        except Exception as exc:
            logger.warning("EOD position review error: %s", exc)


async def _background_loop() -> None:
    await asyncio.sleep(5)
    await _run_market_scan()

    # Run backtest immediately on startup if no results exist yet
    if not _RESULTS_FILE.exists():
        asyncio.create_task(_run_backtest())

    consecutive_errors = 0
    last_day = ""
    last_backtest_day = ""
    last_snapshot_day  = ""
    last_premarket_day = ""
    while True:
        # Reset daily scan stats at midnight
        today = str(date.today())
        if today != last_day:
            _reset_scan_stats_if_needed()
            last_day = today

        # EoD benchmark snapshot at ~15:55 ET (before backtest at 17:00+)
        if _ET is not None and _ALPACA_KEY and _ALPACA_SECRET:
            now_et = datetime.now(_ET)
            if (today != last_snapshot_day
                    and now_et.weekday() < 5
                    and now_et.hour == 15 and now_et.minute >= 55):
                last_snapshot_day = today
                try:
                    async with aiohttp.ClientSession(
                        connector=aiohttp.TCPConnector(
                            resolver=aiohttp.resolver.ThreadedResolver()
                        )
                    ) as _snap_session:
                        await _eod_snapshot(_snap_session)
                except Exception as exc:
                    logger.warning("EoD snapshot task failed: %s", exc)

        # Run backtest once per day after market close (17:00+ ET)
        if _ET is not None:
            now_et = datetime.now(_ET)
            if (today != last_backtest_day
                    and now_et.weekday() < 5
                    and now_et.hour >= 17):
                last_backtest_day = today
                asyncio.create_task(_run_backtest())

        # Pre-market gap scanner: runs once between 9:00–9:25 ET on weekdays
        if _ET is not None and _ALPACA_KEY and _ALPACA_SECRET:
            now_et = datetime.now(_ET)
            if (today != last_premarket_day
                    and now_et.weekday() < 5
                    and now_et.hour == 9 and now_et.minute < 25):
                last_premarket_day = today
                asyncio.create_task(_run_premarket_scan())

        # Backoff: after 3 consecutive errors, wait 10× longer
        wait = 300 if consecutive_errors < 3 else 3000
        await asyncio.sleep(wait)

        try:
            await _run_market_scan()
            consecutive_errors = 0  # reset on success
        except Exception as exc:
            consecutive_errors += 1
            _scan_stats["scan_errors"] += 1
            logger.error(
                "Scanner error #%d (backoff=%ds): %s",
                consecutive_errors, wait, exc,
            )

        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    resolver=aiohttp.resolver.ThreadedResolver()
                )
            ) as session:
                await _check_and_close_trades(session)
                await _revalidate_expired_recs(session)
        except Exception as exc:
            logger.error("Trade-check error: %s", exc)


# === FastAPI app ===

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _trades_lock
    _trades_lock = asyncio.Lock()

    # Open AI4Trade session if client was created
    if _ai4trade_client is not None and _ai4trade_client._session is None:
        try:
            import aiohttp as _aio
            _ai4trade_client._session = _aio.ClientSession(
                timeout=_aio.ClientTimeout(total=15.0)
            )
            if _ai4trade_client.email and _ai4trade_client.password:
                await _ai4trade_client._authenticate()
            logger.info("AI4Trade session opened")
        except Exception as _e:
            logger.warning("AI4Trade session failed to open: %s", _e)

    task     = asyncio.create_task(_background_loop())
    trail    = asyncio.create_task(_trailing_stop_loop())
    exit_mon = asyncio.create_task(_position_exit_monitor_loop())
    eod_rev  = asyncio.create_task(_eod_position_review_loop())
    yield
    task.cancel()
    trail.cancel()
    exit_mon.cancel()
    eod_rev.cancel()
    for t in [task, trail, exit_mon, eod_rev]:
        try:
            await t
        except asyncio.CancelledError:
            pass

    # Close AI4Trade session on shutdown
    if _ai4trade_client is not None and _ai4trade_client._session is not None:
        try:
            await _ai4trade_client._session.close()
        except Exception:
            pass


app = FastAPI(title="Trading Bot API", lifespan=lifespan)
# Locked to the local dashboard by default; set CORS_ALLOW_ORIGINS (comma-separated)
# if the dashboard is served from elsewhere.
_cors_origins = [
    o.strip() for o in os.getenv(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],    allow_headers=["*"],
)


# === REST endpoints ===

@app.get("/api/recommendations", dependencies=[Depends(_verify_bot_secret)])
def get_recommendations():
    data = _load(RECS_FILE, [])
    if isinstance(data, list):
        return data
    return []


@app.get("/api/history", dependencies=[Depends(_verify_bot_secret)])
def get_history():
    data = _load(HISTORY_FILE, [])
    if isinstance(data, list):
        return data
    return []


@app.get("/api/pnl", dependencies=[Depends(_verify_bot_secret)])
def get_pnl():
    # Always compute from trade history (PNL_FILE is not used as a cache)
    history = _load(HISTORY_FILE, [])
    if not isinstance(history, list):
        return []
    from collections import defaultdict
    daily: dict = defaultdict(lambda: {"pnl": 0.0, "count": 0})
    for t in history:
        d = str(t.get("closed_at", t.get("executed_at", ""))[:10])
        if not d:
            continue
        pnl = float(t.get("pnl") or t.get("realized_pnl") or 0)
        daily[d]["pnl"]   += pnl
        daily[d]["count"] += 1
    rows = sorted(daily.items())
    cum  = 0.0
    out  = []
    for date_str, v in rows:
        cum += v["pnl"]
        out.append({
            "date":            date_str,
            "daily_pnl":       round(v["pnl"], 2),
            "cumulative_pnl":  round(cum, 2),
            "trade_count":     v["count"],
        })
    return out


@app.get("/api/stats", dependencies=[Depends(_verify_bot_secret)])
def get_stats():
    all_trades = _load(HISTORY_FILE, [])
    if not isinstance(all_trades, list):
        all_trades = []

    closed  = [t for t in all_trades if t.get("status") == "closed"]
    open_tr = [t for t in all_trades if t.get("status") == "open"]
    today_s = str(date.today())

    total_trades  = len(closed)
    open_count    = len(open_tr)
    pnls          = [float(t.get("pnl") or t.get("realized_pnl") or 0) for t in closed]
    total_pnl     = sum(pnls)
    wins          = [p for p in pnls if p > 0]
    win_rate      = (len(wins) / total_trades * 100) if total_trades else 0.0

    today_pnl = sum(
        float(t.get("pnl") or t.get("realized_pnl") or 0)
        for t in closed
        if str(t.get("closed_at", ""))[:10] == today_s
    )

    # Sharpe, max drawdown, avg R/R
    import numpy as _np
    sharpe = 0.0
    if len(pnls) >= 2:
        arr = _np.array(pnls)
        std = float(_np.std(arr))
        if std > 0:
            sharpe = round(float(_np.mean(arr)) / std * _np.sqrt(252), 2)

    max_dd = 0.0
    if pnls:
        cum  = _np.cumsum(_np.array(pnls))
        peak = _np.maximum.accumulate(cum)
        dd   = cum - peak
        max_dd = round(float(dd.min()), 2)

    rr_vals = [float(t.get("risk_reward") or 0) for t in closed if t.get("risk_reward")]
    avg_rr  = round(sum(rr_vals) / len(rr_vals), 2) if rr_vals else 0.0

    # Best / worst trade
    best_trade  = round(max(pnls), 2)  if pnls else 0.0
    worst_trade = round(min(pnls), 2)  if pnls else 0.0

    # Avg hold time (hours)
    hold_times = []
    for t in closed:
        try:
            opened = t.get("executed_at") or ""
            closed_at = t.get("closed_at") or ""
            if opened and closed_at:
                delta = datetime.fromisoformat(closed_at) - datetime.fromisoformat(opened)
                hold_times.append(delta.total_seconds() / 3600)
        except Exception:
            pass
    avg_hold_hours = round(sum(hold_times) / len(hold_times), 1) if hold_times else 0.0

    weights = _load_weights()
    return {
        "total_pnl":       round(total_pnl, 2),
        "today_pnl":       round(today_pnl, 2),
        "win_rate":        round(win_rate, 1),
        "total_trades":    total_trades,
        "open_positions":  open_count,
        "sharpe_ratio":    sharpe,
        "max_drawdown":    max_dd,
        "avg_rr":          avg_rr,
        "best_trade":      best_trade,
        "worst_trade":     worst_trade,
        "avg_hold_hours":  avg_hold_hours,
        "strategy_version": weights.get("update_count", 0),
        "win_rate_30d":    weights.get("win_rate_30d"),
        "bias":            weights.get("bias", "neutral"),
        "agents_active":   _AGENTS_AVAILABLE,
    }



@app.get("/api/regime", dependencies=[Depends(_verify_bot_secret)])
def get_regime():
    data = _load(REGIME_FILE, {})
    if isinstance(data, dict) and data.get("regime"):
        return data
    return {
        "regime":      "neutral",
        "vix_level":   15.0,
        "spy_day_chg": 0.0,
        "qqq_day_chg": 0.0,
        "rationale":   "no data yet",
        "timestamp":   datetime.utcnow().isoformat(),
    }


@app.get("/api/sectors", dependencies=[Depends(_verify_bot_secret)])
def get_sectors():
    recs = _load(RECS_FILE, [])
    if not isinstance(recs, list):
        return []
    from collections import defaultdict
    bucket_score: dict = defaultdict(list)
    bucket_chg:   dict = defaultdict(list)
    for r in recs:
        sec = r.get("sector", "Other")
        bucket_score[sec].append(float(r.get("composite_score", 50)))
        bucket_chg[sec].append(float(r.get("chg_pct", 0)))
    return sorted(
        [
            {
                "sector": s,
                "score":  round(sum(bucket_score[s]) / len(bucket_score[s]), 1),
                "change": round(sum(bucket_chg[s])   / len(bucket_chg[s]),   2),
                "count":  len(bucket_score[s]),
            }
            for s in bucket_score
        ],
        key=lambda x: x["score"], reverse=True,
    )


class ExecuteBody(BaseModel):
    ticker:            str
    direction:         str
    qty:               int
    entry:             float
    stop_loss:         float
    take_profit:       float
    recommendation_id: Optional[str] = None
    order_id:          Optional[str] = None
    composite_score:   Optional[float] = None
    score:             Optional[float] = None
    evaluations:       Optional[list] = None
    beta:              Optional[float] = None


@app.get("/api/open", dependencies=[Depends(_verify_bot_secret)])
def get_open_context():
    """Return open trade TP/SL context for PositionsTable."""
    trades = _load(HISTORY_FILE, [])
    if not isinstance(trades, list):
        trades = []
    open_trades = [t for t in trades if t.get("status") == "open"]
    return {
        t["id"]: {
            "take_profit": t.get("take_profit"),
            "stop_loss":   t.get("stop_loss"),
            "direction":   t.get("direction"),
            "entry":       t.get("entry"),
            "qty":         t.get("qty"),
        }
        for t in open_trades if "id" in t
    }


@app.post("/api/execute", dependencies=[Depends(_verify_bot_secret)])
async def execute_trade(body: ExecuteBody):
    async with _trades_lock:
        history = _load(HISTORY_FILE, [])
        if not isinstance(history, list):
            history = []

        # Circuit breaker checks
        cb_reason = _check_circuit_breaker()
        if cb_reason:
            _log_rejection(body.ticker, "circuit_breaker", body.composite_score or 0.0,
                           {"circuit_breaker_reason": cb_reason})
            raise HTTPException(status_code=409, detail=cb_reason)

        open_count = len([t for t in history if t.get("status") == "open"])
        if open_count >= MAX_OPEN_POSITIONS:
            _log_rejection(body.ticker, "max_positions", body.composite_score or 0.0,
                           {"open_count": open_count, "max": MAX_OPEN_POSITIONS})
            raise HTTPException(status_code=409, detail=f"Max open positions ({MAX_OPEN_POSITIONS}) reached")

        # Sector correlation guard: max 2 open positions per sector
        ticker_sector = _SECTOR_MAP.get(body.ticker.upper(), "Other")
        open_in_sector = sum(
            1 for t in history
            if t.get("status") == "open"
            and _SECTOR_MAP.get(t.get("ticker", "").upper(), "Other") == ticker_sector
        )
        if open_in_sector >= 2:
            raise HTTPException(status_code=409, detail=f"Sector limit: {open_in_sector} open positions in {ticker_sector}")

        # Portfolio beta cap: net |beta| across all open positions
        portfolio_beta = sum(
            float(t.get("beta", 1.0)) * (1.0 if t.get("direction") == "LONG" else -1.0)
            for t in history if t.get("status") == "open"
        )
        new_beta = float(body.beta or 1.0) * (1.0 if body.direction == "LONG" else -1.0)
        if abs(portfolio_beta + new_beta) > PORTFOLIO_BETA_CAP:
            _log_rejection(body.ticker, "beta_cap", body.composite_score or 0.0,
                           {"portfolio_beta": round(portfolio_beta, 2),
                            "new_beta": round(new_beta, 2),
                            "cap": PORTFOLIO_BETA_CAP})
            raise HTTPException(
                status_code=409,
                detail=f"Portfolio beta cap: net beta {portfolio_beta + new_beta:+.2f} would exceed ±{PORTFOLIO_BETA_CAP}",
            )

        trade = {
            "id":              body.recommendation_id or str(uuid.uuid4()),
            "order_id":        body.order_id or "",
            "ticker":          body.ticker,
            "direction":       body.direction,
            "qty":             body.qty,
            "entry":           body.entry,
            "stop_loss":       body.stop_loss,
            "take_profit":     body.take_profit,
            "composite_score": body.composite_score or body.score,
            "risk_reward":     round(abs(body.take_profit - body.entry) / max(abs(body.entry - body.stop_loss), 0.01), 2),
            "executed_at":     datetime.utcnow().isoformat(),
            "status":          "open",
            "pnl":             None,
            "evaluations":     body.evaluations or [],
            "beta":            body.beta or 1.0,
            "regime":          _load(REGIME_FILE, {}).get("regime", "unknown"),
        }
        history.append(trade)
        _save(HISTORY_FILE, history)

    # Store TP/SL context for auto-close detection
    ctx_data = _load(CONTEXT_FILE, {})
    if not isinstance(ctx_data, dict):
        ctx_data = {}
    ctx_data[body.order_id or trade["id"]] = {
        "ticker":      body.ticker,
        "direction":   body.direction,
        "entry":       body.entry,
        "stop_loss":   body.stop_loss,
        "take_profit": body.take_profit,
        "qty":         body.qty,
        "executed_at": trade["executed_at"],
    }
    _save(CONTEXT_FILE, ctx_data)

    return {"status": "recorded", "trade_id": trade["id"]}


@app.post("/api/scan", dependencies=[Depends(_verify_bot_secret)])
async def trigger_scan():
    asyncio.create_task(_run_market_scan())
    return {"status": "scan_triggered", "timestamp": datetime.utcnow().isoformat()}


@app.post("/api/reset-circuit-breaker", dependencies=[Depends(_verify_bot_secret)])
async def reset_circuit_breaker():
    """Manually reset the circuit breaker after reviewing losses."""
    _circuit_breaker.update({
        "halted":    False,
        "reason":    None,
        "halted_at": None,
    })
    logger.info("Circuit breaker manually reset")
    return {"status": "reset", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/rejections", dependencies=[Depends(_verify_bot_secret)])
def get_rejections(limit: int = 50):
    """Return the last `limit` trade rejection records."""
    try:
        lines = REJECT_LOG.read_text(encoding="utf-8").strip().splitlines()
        records = []
        for line in reversed(lines[-limit * 2:]):
            try:
                records.append(json.loads(line))
            except Exception:
                continue
        return records[-limit:]
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning("rejection log read failed: %s", exc)
        return []


@app.get("/api/snapshots", dependencies=[Depends(_verify_bot_secret)])
def get_snapshots(days: int = 30):
    """Return daily benchmark snapshots (last N days)."""
    try:
        lines = SNAPSHOT_LOG.read_text(encoding="utf-8").strip().splitlines()
        records = []
        for line in lines[-days:]:
            try:
                records.append(json.loads(line))
            except Exception:
                continue
        return records
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning("snapshot log read failed: %s", exc)
        return []


_REPO_ROOT = _HERE.parent  # trading_bot/ -> repo root


@app.get("/api/backtest", dependencies=[Depends(_verify_bot_secret)])
def get_backtest():
    """Return backtest_results.json and backtest_optimal.json from the repo root."""
    def read_json(name: str):
        try:
            return json.loads((_REPO_ROOT / name).read_text())
        except Exception:
            return None

    def read_text(name: str):
        try:
            return (_REPO_ROOT / name).read_text()
        except Exception:
            return None

    return {
        "results":    read_json("backtest_results.json"),
        "optimal":    read_json("backtest_optimal.json"),
        "optimizer":  read_json("optimization_results.json"),  # full grid for heatmaps
        "configText": read_text("OPTIMAL_CONFIG.txt"),
    }


@app.post("/api/backtest/run", dependencies=[Depends(_verify_bot_secret)])
async def run_backtest_now():
    asyncio.create_task(_run_backtest())
    return {"status": "backtest_triggered", "timestamp": datetime.utcnow().isoformat()}


@app.post("/api/optimize/run", dependencies=[Depends(_verify_bot_secret)])
async def run_optimizer_now():
    """Trigger the walk-forward profit optimizer (writes backtest_optimal.json)."""
    if _optimizer_stats.get("running"):
        return {"status": "already_running", "timestamp": datetime.utcnow().isoformat()}
    asyncio.create_task(_run_optimizer())
    return {"status": "optimizer_triggered", "timestamp": datetime.utcnow().isoformat()}


@app.post("/api/optimize/apply", dependencies=[Depends(_verify_bot_secret)])
def apply_optimal_params():
    """Apply the optimizer's best params to LIVE trading — no redeploy.

    Writes the tuned LONG/SHORT thresholds + ATR multiples into
    strategy_weights.json, which the live RiskAgent and PortfolioManager read at
    runtime. Guard: refuses to apply params whose out-of-sample (held-out) profit
    is not positive — those would not be expected to make money live.
    """
    try:
        data = json.loads((_REPO_ROOT / "optimization_results.json").read_text())
    except Exception:
        return {"status": "error", "reason": "no optimizer results found — run the optimizer first"}

    best   = data.get("best") or {}
    params = best.get("params") or {}
    if not params:
        return {"status": "error", "reason": "optimizer results have no best params"}

    metrics   = best.get("oos") or best        # OOS metrics when walk-forward validated
    validated = "oos" in best
    oos_pnl   = metrics.get("total_pnl")
    if oos_pnl is None:
        return {"status": "error", "reason": "optimizer results missing profit metric"}
    if oos_pnl <= 0:
        return {
            "status": "rejected",
            "reason": f"{'out-of-sample' if validated else 'backtest'} profit is "
                      f"${oos_pnl:.0f} (not positive) — refusing to apply params that "
                      f"would not be profitable live. Re-run with a longer --days or wider grid.",
        }

    mapping = {
        "LONG_THRESHOLD":      "long_threshold",
        "SHORT_THRESHOLD":     "short_threshold",
        "ATR_STOP_MULTIPLE":   "atr_stop_multiple",
        "ATR_TARGET_MULTIPLE": "atr_target_multiple",
    }
    weights = _load_weights()
    applied: Dict[str, float] = {}
    for src, dst in mapping.items():
        if src in params:
            weights[dst] = float(params[src])
            applied[dst] = float(params[src])
    weights["applied_from_optimizer_at"] = datetime.utcnow().isoformat()
    weights["applied_oos_pnl"]           = round(float(oos_pnl), 2)
    weights["live_tuning_active"]        = True   # let the live bot honor these params
    _save_weights(weights)

    logger.info("Applied optimizer params to live config: %s (OOS PnL=$%.0f)", applied, oos_pnl)
    return {
        "status":    "applied",
        "applied":   applied,
        "oos_pnl":   round(float(oos_pnl), 2),
        "validated": validated,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/optimize/applied", dependencies=[Depends(_verify_bot_secret)])
def get_applied_params():
    """Show which tuned params are currently live (from strategy_weights.json)."""
    w = _load_weights()
    return {
        "long_threshold":     w.get("long_threshold"),
        "short_threshold":    w.get("short_threshold"),
        "atr_stop_multiple":  w.get("atr_stop_multiple"),
        "atr_target_multiple": w.get("atr_target_multiple"),
        "applied_from_optimizer_at": w.get("applied_from_optimizer_at"),
        "applied_oos_pnl":    w.get("applied_oos_pnl"),
    }


@app.post("/api/challenge/run", dependencies=[Depends(_verify_bot_secret)])
async def run_challenge_now(mode: str = "run"):
    """Trigger the AI4Trade challenge runner. mode: run | list | status."""
    if _challenge_stats.get("running"):
        return {"status": "already_running", "timestamp": datetime.utcnow().isoformat()}
    asyncio.create_task(_run_challenge(mode if mode in ("run", "list", "status") else "run"))
    return {"status": "challenge_triggered", "mode": mode, "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/challenges", dependencies=[Depends(_verify_bot_secret)])
def get_challenges():
    """Return the latest challenge results + runner status."""
    results = None
    try:
        results = json.loads((_REPO_ROOT / "challenge_results.json").read_text())
    except Exception:
        pass
    return {"results": results, "status": _challenge_stats}


@app.get("/api/scan-stats", dependencies=[Depends(_verify_bot_secret)])
def get_scan_stats():
    """Return today's scan activity counters and market status."""
    all_trades = _load(HISTORY_FILE, [])
    if not isinstance(all_trades, list):
        all_trades = []
    open_count = len([t for t in all_trades if t.get("status") == "open"])
    return {
        **_scan_stats,
        "market_open":    _is_market_open(),
        "max_positions":  MAX_OPEN_POSITIONS,
        "open_positions": open_count,
        "agents_active":  _AGENTS_AVAILABLE,
        "circuit_breaker": _circuit_breaker,
    }


@app.get("/api/health")
def health():
    gemini_set    = bool(os.getenv("GEMINI_API_KEY"))
    anthropic_set = bool(os.getenv("ANTHROPIC_API_KEY"))
    ai4_set       = bool(os.getenv("AI4TRADE_EMAIL") and os.getenv("AI4TRADE_PASSWORD"))
    execute_live  = os.getenv("EXECUTE_LIVE", "false").lower() in ("1", "true", "yes")
    alpaca_paper  = os.getenv("ALPACA_PAPER", "true").lower() not in ("0", "false", "no")
    return {
        "status":    "ok",
        "agents":    _AGENTS_AVAILABLE,
        "timestamp": datetime.utcnow().isoformat(),
        "backtest":  _backtest_stats,
        "optimizer": _optimizer_stats,
        "challenge": _challenge_stats,
        "circuit_breaker": _circuit_breaker,
        "trading": {
            "execute_live": execute_live,
            "paper_mode":   alpaca_paper,
            "broker":       os.getenv("BROKER", "alpaca"),
            "mode_label":   (
                "LIVE PAPER" if execute_live and alpaca_paper else
                "LIVE REAL"  if execute_live and not alpaca_paper else
                "DRY RUN"
            ),
        },
        "keys": {
            "gemini":    gemini_set,
            "anthropic": anthropic_set,
            "ai4trade":  ai4_set,
            # Vision needs ANY vision-capable LLM key; social needs AI4Trade creds.
            "vision_ready": gemini_set or anthropic_set,
            "social_ready": ai4_set,
        },
    }


@app.get("/api/agent-attribution", dependencies=[Depends(_verify_bot_secret)])
def get_agent_attribution():
    """Return per-agent win/loss counts and total PnL for performance attribution."""
    attr = _load(AGENT_PERF_FILE, {})
    if not isinstance(attr, dict):
        return {}
    result = {}
    for role, stats in attr.items():
        wins   = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        total  = wins + losses
        result[role] = {
            "wins":      wins,
            "losses":    losses,
            "total":     total,
            "win_rate":  round(wins / total * 100, 1) if total > 0 else 0.0,
            "total_pnl": round(stats.get("total_pnl", 0.0), 2),
        }
    return result


@app.get("/api/monte-carlo", dependencies=[Depends(_verify_bot_secret)])
def get_monte_carlo(n_sims: int = 10_000):
    """Monte Carlo resample of trade win/loss sequence → 95% CI on win rate and PnL."""
    import random as _rand
    trades = _load(HISTORY_FILE, [])
    if not isinstance(trades, list):
        return {"error": "no_data"}
    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None]
    n = len(closed)
    if n < 10:
        return {"error": "insufficient_data", "min_trades": 10, "current": n}

    pnls   = [float(t["pnl"]) for t in closed]
    wins   = sum(1 for p in pnls if p > 0)
    actual_wr = wins / n

    sim_wrs  = []
    sim_pnls = []
    for _ in range(n_sims):
        sample  = [_rand.choice(pnls) for _ in range(n)]
        sim_wrs.append(sum(1 for p in sample if p > 0) / n)
        sim_pnls.append(sum(sample))

    sim_wrs.sort()
    sim_pnls.sort()

    return {
        "actual_win_rate":   round(actual_wr * 100, 1),
        "ci_95_lo":          round(sim_wrs[int(0.025 * n_sims)] * 100, 1),
        "ci_95_hi":          round(sim_wrs[int(0.975 * n_sims)] * 100, 1),
        "pnl_p5":            round(sim_pnls[int(0.05  * n_sims)], 2),
        "pnl_p50":           round(sim_pnls[int(0.50  * n_sims)], 2),
        "pnl_p95":           round(sim_pnls[int(0.95  * n_sims)], 2),
        "n_trades":          n,
        "n_sims":            n_sims,
        "skill_signal":      actual_wr > sim_wrs[int(0.10 * n_sims)],  # above 10th-percentile random
    }


@app.get("/api/regime-performance", dependencies=[Depends(_verify_bot_secret)])
def get_regime_performance():
    """Per-regime trade performance breakdown."""
    from collections import defaultdict as _dd
    trades = _load(HISTORY_FILE, [])
    if not isinstance(trades, list):
        return {}
    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None]
    buckets: dict = _dd(list)
    for t in closed:
        buckets[t.get("regime", "unknown")].append(float(t["pnl"]))
    result = {}
    for regime, pnls in buckets.items():
        wins = [p for p in pnls if p > 0]
        result[regime] = {
            "trades":    len(pnls),
            "wins":      len(wins),
            "win_rate":  round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl":   round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        }
    return result


@app.get("/api/exit-decisions", dependencies=[Depends(_verify_bot_secret)])
def get_exit_decisions():
    """Rolling log of exit-monitor and EOD review decisions (newest first)."""
    return list(reversed(_EXIT_DECISIONS))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=False)
