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
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
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
PORTFOLIO_BETA_CAP = float(os.getenv("PORTFOLIO_BETA_CAP", "5.0"))  # max net |beta| across open positions

# End-of-day position review
ALLOW_OVERNIGHT           = os.getenv("ALLOW_OVERNIGHT", "false").lower() in ("1", "true", "yes")
EOD_REVIEW_MIN_BEFORE     = int(os.getenv("EOD_REVIEW_MIN_BEFORE", "25"))       # minutes before 16:00 ET to run EOD review

# Strategy-improvement loop: periodically re-tune agent weights and refresh the
# per-agent scorecard, so learning keeps adapting on a cadence (not only the
# instant a trade closes).
STRATEGY_LOOP_INTERVAL_MIN = int(os.getenv("STRATEGY_LOOP_INTERVAL_MIN", "60"))

# Autonomous paper executor (Railway). OFF by default. When armed it places
# Alpaca PAPER bracket orders for strong recommendations, applying the SAME
# entry guards as /api/execute. Arm on Railway ONLY when no PC bot is running
# the same account — otherwise both venues trade it and double up.
AUTO_EXECUTE_ON_RAILWAY = os.getenv("AUTO_EXECUTE_ON_RAILWAY", "false").lower() in ("1", "true", "yes")
AUTO_EXEC_POLL_MIN      = int(os.getenv("AUTO_EXEC_POLL_MIN", "5"))      # how often to sweep recs
AUTO_EXEC_MIN_SCORE     = float(os.getenv("AUTO_EXEC_MIN_SCORE", "60"))  # LONG >= this; SHORT <= 100-this

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
    "running":          False,
}

import secrets as _secrets
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Literal

_BOT_API_SECRET = os.getenv("BOT_API_SECRET", "")


async def _verify_bot_secret(x_bot_secret: str = Header(default="")) -> None:
    if not _BOT_API_SECRET:
        return  # secret not configured — open in dev; Railway sets it in prod
    if not _secrets.compare_digest(x_bot_secret, _BOT_API_SECRET):
        raise HTTPException(status_code=401, detail="Invalid bot secret")

logger = logging.getLogger("api_server")
logging.basicConfig(level=logging.INFO)

if not _BOT_API_SECRET:
    logger.warning(
        "BOT_API_SECRET is not set — all /api/* endpoints are publicly accessible. "
        "Set this env var in Railway (and Vercel) before going live."
    )

# === Agent imports (lazy -- fallback to simple formula if unavailable) ===

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from core.paths import volume_dir
# Persistent volume (Railway) when attached — keeps runtime data across deploys.
_VOLUME = volume_dir()

_AGENTS_AVAILABLE = False
_pm                = None   # PortfolioManager — the SAME composition live/backtest use
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
    from data.telegram_publisher import TelegramPublisher as _TelegramPublisher

    _settings = load_settings()
    _pm = build_manager(_settings, broker=None)
    # Populate the health board with any missing-config issues up front, so the
    # dashboard can tell the operator what the bot needs (keys, account, etc.).
    try:
        from bootstrap import preflight_checks
        preflight_checks(_settings)
    except Exception:
        logger.debug("preflight_checks failed", exc_info=True)
    # Dashboard scans fetch ~100-bar windows (vs 200 live) — keep the lower
    # bar requirement this endpoint has always used.
    _pm.technical.min_bars = 30
    _Decision = Decision
    _AGENTS_AVAILABLE = True
    _telegram = _TelegramPublisher(
        bot_token=_settings.telegram_bot_token,
    )
    logger.info("Agent pipeline loaded via bootstrap.build_manager — unified with live/backtest")

except Exception as _import_err:
    logger.warning("Agent imports failed (%s) -- scanner using fallback formula", _import_err)


async def _evaluate(ctx: "AnalysisContext"):
    """Run the unified PortfolioManager pipeline on one ticker.

    Same agents, weights, and composite as live trading and backtests
    (bootstrap.build_manager). Renders the chart for the vision agent,
    and bounds total evaluation time so a slow LLM can't stall the scan.

    Returns a TradeDecision, or None on failure/timeout.
    """
    if not _AGENTS_AVAILABLE or _pm is None:
        return None

    from dataclasses import replace
    # The dashboard is an analysis view, not live execution — don't fail-closed on
    # stale bars (after hours the last bar is naturally old), so Risk still shows a
    # plan on the most recent close. live_runner keeps enforce_freshness=True.
    ctx = replace(ctx, enforce_freshness=False)

    # Build chart image path for VisionAgent (render async in thread)
    chart_path = None
    if _pm.vision is not None and ctx.bars is not None:
        try:
            from data.chart_renderer import render_chart
            chart_path = await asyncio.to_thread(render_chart, ctx.ticker, ctx.bars)
            ctx = replace(ctx, chart_image_path=chart_path)
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

_ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() not in ("false", "0", "no")
_BROKER_BASE  = "https://paper-api.alpaca.markets" if _ALPACA_PAPER else "https://api.alpaca.markets"
_DATA_BASE    = "https://data.alpaca.markets"

_SECTOR_MAP: Dict[str, str] = {
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "GOOGL": "Technology", "META": "Technology", "AMZN": "Consumer",
    "TSLA": "Consumer", "AMD": "Technology", "INTC": "Technology",
    "NFLX": "Communication", "JPM": "Financials", "BAC": "Financials",
    "GS": "Financials", "XOM": "Energy", "CVX": "Energy",
    "JNJ": "Healthcare", "PFE": "Healthcare", "UNH": "Healthcare",
}


# === Persistent storage ===

DATA_DIR    = (_VOLUME / "data") if _VOLUME else (_HERE / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
RECS_FILE     = DATA_DIR / "recommendations.json"
SCAN_LOG_FILE = DATA_DIR / "scan_log.json"
TRADES_FILE  = DATA_DIR / "trades.json"
HISTORY_FILE = TRADES_FILE                        # alias — executed trades = history
PNL_FILE     = DATA_DIR / "pnl.json"
CONTEXT_FILE = DATA_DIR / "context.json"
WEIGHTS_FILE = DATA_DIR / "strategy_weights.json"
REGIME_FILE  = DATA_DIR / "regime.json"
TRADE_MODE_FILE = DATA_DIR / "trade_mode.json"   # auto-execute toggle (shared with live_runner)
BROKER_MODE_FILE = DATA_DIR / "broker_mode.json" # alpaca/ibkr toggle (shared with live_runner)
REJECT_LOG      = DATA_DIR / "risk_rejections.jsonl"
SNAPSHOT_LOG    = DATA_DIR / "daily_snapshots.jsonl"
AGENT_PERF_FILE = DATA_DIR / "agent_attribution.json"
# WeightTuner output (online learning). These live at the repo-relative data dir
# because that is where core.weight_tuner / PortfolioManager._live_weight read and
# write them — keep this in lockstep with weight_tuner._WEIGHTS_FILE/_HISTORY_FILE.
LEARNING_HISTORY_FILE = _HERE / "data" / "learning_history.jsonl"
LEARNING_WEIGHTS_FILE = _HERE / "data" / "strategy_weights.json"
# Per-agent scorecard written by the strategy-improvement loop (served at /api/agent-scorecards).
AGENT_SCORECARDS_FILE = _HERE / "data" / "agent_scorecards.json"
EARNINGS_CACHE: Dict[str, Any] = {"blacklist": set(), "updated_at": None}


def _drive_weight_tuner(trades: List[dict]) -> None:
    """Re-run the online WeightTuner from closed trades (server-side learning).

    Called whenever a trade closes so the agent weights adapt and a snapshot is
    appended to learning_history.jsonl — the data the /api/learning view renders.
    No-ops cleanly when the agent pipeline is unavailable.
    """
    if not _AGENTS_AVAILABLE or _pm is None:
        return
    closed = [t for t in trades if t.get("status") == "closed" and t.get("evaluations")]
    try:
        _pm._tuner.update_from_trades(closed)
    except Exception:
        logger.debug("weight tuner update failed", exc_info=True)


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _trade_key(trade: dict) -> Optional[str]:
    """Stable unique key for a trade record. `id` is always set by /api/execute;
    `order_id` is a defensive fallback for any legacy record without one."""
    return trade.get("id") or trade.get("order_id") or None


def _merge_trade_changes(disk: list, snapshot: list, changed_ids: set) -> list:
    """Overlay only the trades this task changed onto the latest on-disk list.

    The background loops load trades.json, mutate a snapshot, then save. Because
    they run as separate asyncio tasks against the SAME file, a blind save of the
    whole snapshot clobbers trades another task opened or closed in the meantime
    (lost/resurrected positions). Instead, reload under the lock and replace only
    the records this task actually touched (by id); every other on-disk record —
    including ones added concurrently — is preserved untouched.
    """
    if not changed_ids:
        return disk
    snap_by_id = {k: t for t in snapshot if (k := _trade_key(t)) is not None}
    out: list = []
    seen: set = set()
    for t in disk:
        k = _trade_key(t)
        if k in changed_ids and k in snap_by_id:
            out.append(snap_by_id[k])
            seen.add(k)
        else:
            out.append(t)
    # A changed trade missing from disk (rare) is appended so it isn't dropped.
    for k in changed_ids:
        if k not in seen and k in snap_by_id:
            out.append(snap_by_id[k])
    return out


async def _save_trade_changes(snapshot: list, changed_ids: set) -> list:
    """Atomically merge this task's changed trades into trades.json under the lock.

    Returns the merged list so callers can drive downstream learning off the
    authoritative state rather than their stale snapshot.
    """
    async with _trades_lock:
        disk = _load(TRADES_FILE, [])
        if not isinstance(disk, list):
            disk = []
        merged = _merge_trade_changes(disk, snapshot, changed_ids)
        _save(TRADES_FILE, merged)
    return merged



def _load_trade_mode() -> Dict[str, Any]:
    """Read the runtime auto-execute toggle.

    Default ``auto_execute=False`` (manual): the bot generates signals but
    never places orders itself — the user approves each trade on the site.
    When ``True``, live_runner auto-executes entries (still requires
    EXECUTE_LIVE=true on the bot server for any order to leave the building).
    """
    data = _load(TRADE_MODE_FILE, {})
    if not isinstance(data, dict):
        return {"auto_execute": False}
    return {"auto_execute": bool(data.get("auto_execute", False))}


def _load_broker_mode() -> Dict[str, Any]:
    """Read the runtime broker selection (alpaca vs ibkr).

    The dashboard toggle (broker_mode.json) wins; otherwise the BROKER env
    default applies. live_runner reads the same file and restarts its trading
    session on the newly-selected broker when it changes.
    """
    default = os.getenv("BROKER", "alpaca").lower()
    if default not in ("alpaca", "ibkr"):
        default = "alpaca"
    data = _load(BROKER_MODE_FILE, {})
    if isinstance(data, dict):
        choice = str(data.get("broker", "")).lower()
        if choice in ("alpaca", "ibkr"):
            return {"broker": choice}
    return {"broker": default}


def _log_rejection(ticker: str, reason: str, score: float, details: dict) -> None:
    """Append a trade rejection record to risk_rejections.jsonl."""
    entry = {
        "ts":              datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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
    trade["closed_at"]   = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


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
    now = datetime.now(timezone.utc).replace(tzinfo=None)
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
    start = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

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

    changed_ids: set = set()
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
                trade["closed_at"]   = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                changed_ids.add(_trade_key(trade))
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
                trade["closed_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                changed_ids.add(_trade_key(trade))
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
            trade["closed_at"]   = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            changed_ids.add(_trade_key(trade))
            _update_agent_attribution(trade)
            logger.info("Closed %s %s via %s: exit=%.2f PnL=$%.2f (%.2f%%)",
                        direction, trade["ticker"], exit_reason, exit_price, pnl, pnl_pct)

        except Exception as exc:
            logger.debug("Could not check order %s: %s", trade.get("order_id"), exc)

    if changed_ids:
        merged = await _save_trade_changes(trades, changed_ids)
        _drive_weight_tuner(merged)


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
    weights["last_updated"]   = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    # NOTE: the self-tuner refines ATR/score params but does NOT activate live
    # tuning on its own — only a deliberate, OOS-validated optimizer Apply flips
    # live_tuning_active (avoids stepping live sizing from the DEFAULT baseline).
    # Once Apply has activated tuning, these refinements build on the applied values.
    # Self-tuner skips any field the user has manually locked.
    locked: set = set(weights.get("manual_overrides") or {})

    if win_rate > 0.60:
        if "min_score"          not in locked:
            weights["min_score"]          = max(30,  weights["min_score"] - 1)
        if "time_window_minutes" not in locked:
            weights["time_window_minutes"] = min(60,  weights["time_window_minutes"] + 2)
        if "atr_target_multiple" not in locked:
            weights["atr_target_multiple"] = min(5.0, weights["atr_target_multiple"] * 1.03)
        if "chg_weight"          not in locked:
            weights["chg_weight"]          = min(10.0, weights["chg_weight"] * 1.02)
    elif win_rate < 0.40:
        if "min_score"          not in locked:
            weights["min_score"]          = min(70,  weights["min_score"] + 2)
        if "time_window_minutes" not in locked:
            weights["time_window_minutes"] = max(20,  weights["time_window_minutes"] - 5)
        if "atr_stop_multiple"  not in locked:
            weights["atr_stop_multiple"]  = max(1.0, weights["atr_stop_multiple"] * 0.95)
        if "chg_weight"          not in locked:
            weights["chg_weight"]         = max(1.5, weights["chg_weight"] * 0.95)
        if "intra_weight"        not in locked:
            weights["intra_weight"]       = max(0.5, weights["intra_weight"] * 0.97)

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

    now     = datetime.now(timezone.utc).replace(tzinfo=None)
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
    "running":       False,
    "last_log":      None,   # full stdout from last run
    "log_lines":     [],     # live lines while running
}

_optimizer_stats: Dict[str, Any] = {
    "last_run_at":   None,
    "last_status":   None,   # "ok" | "failed" | "timeout"
    "error_count":   0,
    "last_error":    None,
    "running":       False,
    "last_log":      None,   # full stdout from last run
    "log_lines":     [],     # live lines while running
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
                                  "halted_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()})
        return reason

    # Daily P&L check
    daily_loss = _daily_pnl_pct()
    _circuit_breaker["daily_pnl_pct"] = round(daily_loss * 100, 2)
    if daily_loss <= -DAILY_LOSS_LIMIT_PCT:
        reason = f"Daily loss limit hit ({daily_loss*100:.1f}%) — trading halted for today"
        _circuit_breaker.update({"halted": True, "reason": "daily_loss",
                                  "halted_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()})
        return reason

    _circuit_breaker["halted"] = False
    _circuit_breaker["reason"] = None
    return None


async def _fetch_news_catalyst(session: aiohttp.ClientSession, sym: str) -> str:
    """Return the most recent Benzinga news headline for sym from last 24h, or ''."""
    try:
        since = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
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
                        "id":              f"{sym}-pm-{int(datetime.now(timezone.utc).replace(tzinfo=None).timestamp())}",
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
                        "scanned_at":      datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                        "expires_at":      expires_at,
                        "reeval_count":    0,
                        "hot_sector":      False,
                        "evaluations":     [],
                        "timestamp":       datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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


async def _run_market_scan(force: bool = False) -> None:
    """Scan the universe and refresh recommendations/agent readings.

    ``force=True`` (a manual scan from the dashboard button) bypasses the
    off-hours hourly throttle so a click always runs a fresh scan.
    """
    if _scan_stats.get("running"):
        return
    _scan_stats["running"] = True
    try:
        await _run_market_scan_inner(force=force)
    finally:
        _scan_stats["running"] = False


async def _run_market_scan_inner(force: bool = False) -> None:
    _reset_scan_stats_if_needed()

    if not _ALPACA_KEY or not _ALPACA_SECRET:
        logger.warning("Alpaca credentials missing -- skipping auto-scan")
        return

    if not _is_market_open():
        # Off-hours: throttle to one scan per hour. Each scan runs LLM agents
        # on ~20 symbols; every 5 min all night burns quota for stale signals.
        last = _scan_stats.get("last_scan_at")
        if last and not force:
            try:
                age_min = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(last)).total_seconds() / 60
                if age_min < 60:
                    return
            except Exception:
                pass
        _scan_stats["market_closed_skips"] += 1
        logger.info("Market closed — %s scan (#%d today)",
                    "manual" if force else "hourly off-hours", _scan_stats["market_closed_skips"])

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
            "timestamp":   datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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

        recs:     List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []

        def _rej(sym: str, reason: str, price: float = 0.0, chg_pct: float = 0.0, score: float = 0.0) -> None:
            rejected.append({
                "ticker":      sym,
                "price":       round(price, 2),
                "chg_pct":     round(chg_pct, 2),
                "score":       round(score, 1) if score else None,
                "skip_reason": reason,
                "scanned_at":  datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            })

        for sym in symbols_raw:
            snap = snaps.get(sym) or {}
            if not snap:
                _rej(sym, "No snapshot data")
                continue

            if sym in earnings_blacklist:
                _scan_stats["recs_skipped"] += 1
                _rej(sym, "Earnings blackout")
                continue

            daily_bar  = snap.get("dailyBar")     or {}
            prev_bar   = snap.get("prevDailyBar") or {}
            latest_trd = snap.get("latestTrade")  or {}

            price      = float(latest_trd.get("p") or daily_bar.get("c") or 0)
            prev_close = float(prev_bar.get("c") or price)
            day_open   = float(daily_bar.get("o") or price)

            if price < 5 or price > 2000:
                _rej(sym, f"Price out of range (${price:.2f})", price=price)
                continue

            chg_pct   = (price - prev_close) / prev_close * 100 if prev_close else 0
            intra_pct = (price - day_open)   / day_open   * 100 if day_open   else 0

            if abs(chg_pct) < min_chg:
                _rej(sym, f"Low movement ({chg_pct:+.1f}%)", price=price, chg_pct=chg_pct)
                continue

            df         = bars_map.get(sym)
            agent_used = False
            rationale  = ""
            evaluations_out = []

            if _AGENTS_AVAILABLE and df is not None and len(df) >= 20:
                ctx      = AnalysisContext(ticker=sym, bars=df, account={"equity": equity},
                                          hourly_bars=hourly_map.get(sym))
                decision = await _evaluate(ctx)
                if decision is not None:
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

                    # Dashboard "ideas" gate is looser than the live bot's trade gate.
                    # When PM already decided LONG/SHORT (strict threshold passed),
                    # show it directly. Otherwise apply the dashboard's own looser
                    # threshold (>53 / <47) so mildly directional agent composites
                    # still surface as ideas (the live bot applies its own stricter
                    # gate before actually submitting a bracket order).
                    if decision.is_actionable:
                        direction = decision.decision.value  # LONG or SHORT
                    elif score > 53:
                        direction = "LONG"
                    elif score < 47:
                        direction = "SHORT"
                    else:
                        # Agent returned a neutral/uncertain score — fall through to
                        # the price-momentum fallback instead of dropping the ticker.
                        # This commonly happens when the LLM is throttled and FinBERT
                        # gives conservative mid-range composites for most stocks.
                        logger.debug("%s score=%.1f neutral — using price fallback", sym, score)
                        agent_used = False

                    # Cap min_score at 55 so adaptive tuning can't choke all signals.
                    effective_min = min(min_score, 55)
                    if score < effective_min:
                        _rej(sym, f"Below min score ({score:.1f} < {effective_min})", price=price, chg_pct=chg_pct, score=score)
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
                    _rej(sym, "Agent evaluation failed", price=price, chg_pct=chg_pct)
                    # agent_used stays False; fallback formula runs below

            if not agent_used:
                # Fallback formula: agents unavailable, no bars, or agent timed out.
                chg_w   = weights.get("chg_weight", 4.0)
                intra_w = weights.get("intra_weight", 2.0)
                score   = min(max(50 + chg_pct * chg_w + intra_pct * intra_w, score_floor), score_ceil)
                if score < min_score:
                    _rej(sym, f"Fallback score too low ({score:.1f})", price=price, chg_pct=chg_pct, score=score)
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
            expires_base = datetime.now(timezone.utc).replace(tzinfo=None) if _is_market_open() else _next_market_open()
            expires_iso  = (expires_base + timedelta(minutes=win_mins)).isoformat()

            recs.append({
                "id":              f"{sym}-{int(datetime.now(timezone.utc).replace(tzinfo=None).timestamp())}",
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
                "scanned_at":   datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "expires_at":   expires_iso,
                "reeval_count": 0,
                "hot_sector":   False,
                "evaluations":  evaluations_out,
                "timestamp":    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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
        if recs:
            _save(RECS_FILE, recs)
        # If scan produced nothing, keep whatever is on disk.
        # Revalidation prunes stale recs during market hours;
        # EOD extension (in _background_loop) keeps valid ones visible overnight.
        _save(SCAN_LOG_FILE, {
            "picked":     recs,
            "rejected":   rejected,
            "scanned_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        })

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
        _scan_stats["last_scan_at"]     = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
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

_BACKTEST_SCRIPT  = _HERE / "backtest_intraday.py"
_RESULTS_FILE     = (_VOLUME or _HERE.parent) / "backtest_results.json"
_BACKTEST_INTERVAL_H = int(os.getenv("BACKTEST_INTERVAL_H", "24"))


async def _run_backtest() -> None:
    """Run backtest_intraday.py as a subprocess (non-blocking)."""
    if _backtest_stats.get("running"):
        return
    if not _BACKTEST_SCRIPT.exists():
        logger.warning("backtest_intraday.py not found — skipping auto-backtest")
        return
    _backtest_stats["running"] = True
    logger.info("Auto-backtest starting (interval=%dh)…", _BACKTEST_INTERVAL_H)
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(_BACKTEST_SCRIPT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_HERE),
        )
        log_lines: list[str] = []
        _backtest_stats["log_lines"] = log_lines
        deadline = asyncio.get_event_loop().time() + 1800  # 30 min

        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            log_lines.append(line)
            if len(log_lines) > 2000:
                log_lines.pop(0)
            if asyncio.get_event_loop().time() > deadline:
                proc.kill()
                raise asyncio.TimeoutError

        await proc.wait()
        decoded = "\n".join(log_lines)
        if proc.returncode == 0:
            logger.info("Auto-backtest complete — results written to %s", _RESULTS_FILE)
            _backtest_stats.update({
                "last_run_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "last_status": "ok",
                "last_error":  None,
                "last_log":    decoded,
            })
        else:
            logger.error("Auto-backtest failed (rc=%d): %s", proc.returncode, decoded[-500:])
            _backtest_stats.update({
                "last_run_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "last_status": "failed",
                "error_count": _backtest_stats["error_count"] + 1,
                "last_error":  decoded[-500:],
                "last_log":    decoded,
            })
    except asyncio.TimeoutError:
        logger.error("Auto-backtest timed out after 30 min — killed")
        if proc is not None:
            proc.kill()
        _backtest_stats.update({
            "last_run_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "last_status": "timeout",
            "error_count": _backtest_stats["error_count"] + 1,
            "last_error":  "timed out after 30 min",
            "last_log":    _backtest_stats.get("last_log", "") or "",
        })
    except Exception:
        logger.exception("Auto-backtest subprocess error")
        _backtest_stats.update({
            "last_run_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "last_status": "failed",
            "error_count": _backtest_stats["error_count"] + 1,
        })
    finally:
        _backtest_stats["running"] = False


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
    _optimizer_stats["log_lines"] = []
    logger.info("Optimizer starting…")
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(_OPTIMIZER_SCRIPT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_HERE),
        )

        # Stream stdout line-by-line so the dashboard can show live progress
        log_lines: list[str] = _optimizer_stats["log_lines"]
        deadline = asyncio.get_event_loop().time() + 3000  # 50 min

        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            log_lines.append(line)
            if len(log_lines) > 1000:
                log_lines.pop(0)
            if asyncio.get_event_loop().time() > deadline:
                proc.kill()
                raise asyncio.TimeoutError

        await proc.wait()
        full_log = "\n".join(log_lines)

        if proc.returncode == 0:
            logger.info("Optimizer complete — wrote backtest_optimal.json + OPTIMAL_CONFIG.txt")
            _optimizer_stats.update({
                "last_run_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "last_status": "ok",
                "last_error":  None,
                "last_log":    full_log,
            })
        else:
            logger.error("Optimizer failed (rc=%d): %s", proc.returncode, full_log[-500:])
            _optimizer_stats.update({
                "last_run_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "last_status": "failed",
                "error_count": _optimizer_stats["error_count"] + 1,
                "last_error":  full_log[-500:],
                "last_log":    full_log,
            })
    except asyncio.TimeoutError:
        logger.error("Optimizer timed out after 50 min — killed")
        if proc is not None:
            proc.kill()
        _optimizer_stats.update({
            "last_run_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "last_status": "timeout",
            "error_count": _optimizer_stats["error_count"] + 1,
            "last_error":  "timed out after 50 min",
        })
    except Exception:
        logger.exception("Optimizer subprocess error")
        _optimizer_stats.update({
            "last_run_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "last_status": "failed",
            "error_count": _optimizer_stats["error_count"] + 1,
        })
    finally:
        _optimizer_stats["running"] = False


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

                changed_ids: set = set()
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
                            changed_ids.add(_trade_key(trade))
                            logger.debug("Trail stop updated %s LONG stop %.2f -> %.2f",
                                         ticker, stop, new_stop)
                        # Check if current price hit the stop
                        effective_stop = max(stop, new_stop) if new_stop > stop else stop
                        if price <= effective_stop:
                            _close_simulated_trade(trade, effective_stop, "trailing_stop")
                            changed_ids.add(_trade_key(trade))
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
                            changed_ids.add(_trade_key(trade))
                            logger.debug("Trail stop updated %s SHORT stop %.2f -> %.2f",
                                         ticker, stop, new_stop)
                        effective_stop = min(stop, new_stop) if new_stop < stop else stop
                        if price >= effective_stop:
                            _close_simulated_trade(trade, effective_stop, "trailing_stop")
                            changed_ids.add(_trade_key(trade))
                            logger.info("Trailing stop hit: %s SHORT closed @ %.2f", ticker, effective_stop)

                if changed_ids:
                    await _save_trade_changes(trades, changed_ids)

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
        "ts":        datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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
    if _telegram is not None:
        asyncio.create_task(_telegram.send_trade_exit(trade, price, reason))
    return True


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

                changed_ids: set = set()
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
                            changed_ids.add(_trade_key(trade))
                    else:
                        _log_exit_decision(ticker, direction, "hold_overnight", reason, score=score, price=p)
                        kept.append(ticker)
                        logger.info("EOD review: KEPT %s %s overnight (score %.0f)", direction, ticker, score or 0)

                if changed_ids:
                    await _save_trade_changes(trades, changed_ids)

                logger.info(
                    "EOD review done — closed: %s | kept overnight: %s",
                    closed or "none", kept or "none",
                )

        except Exception as exc:
            logger.warning("EOD position review error: %s", exc)


def _refresh_agent_scorecards() -> List[dict]:
    """Recompute each agent's track record from closed trades and persist it.

    Reads the live strategy_weights.json so the surfaced weight/multiplier is the
    one actually in force. Fail-soft: returns [] and writes nothing on error.
    """
    from core.agent_scorecard import compute_agent_scorecards

    trades = _load(TRADES_FILE, [])
    closed = [
        t for t in trades
        if isinstance(t, dict) and t.get("status") == "closed" and t.get("pnl") is not None
    ] if isinstance(trades, list) else []
    weights = _load(LEARNING_WEIGHTS_FILE, {})
    cards = compute_agent_scorecards(closed, weights if isinstance(weights, dict) else {})
    _save(AGENT_SCORECARDS_FILE, {
        "updated_at":    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "sample_trades": len(closed),
        "agents":        cards,
    })
    return cards


async def _strategy_improvement_loop() -> None:
    """Periodically improve each agent: re-tune weights, refresh the scorecard.

    The WeightTuner otherwise runs only the instant a trade closes. This loop
    re-runs it on a cadence so the live weights/thresholds keep adapting during
    quiet periods (write-through to strategy_weights.json, which PortfolioManager
    reads), and refreshes the per-agent scorecard served at /api/agent-scorecards.
    """
    interval = max(60, STRATEGY_LOOP_INTERVAL_MIN * 60)
    while True:
        await asyncio.sleep(interval)
        try:
            trades = _load(TRADES_FILE, [])
            if isinstance(trades, list):
                _drive_weight_tuner(trades)          # write-through: adapts live weights
            cards = _refresh_agent_scorecards()
            logger.info("Strategy loop: re-tuned weights; scored %d agents", len(cards))
        except Exception as exc:
            logger.warning("Strategy improvement loop error: %s", exc)


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
    last_eod_extend_day = ""
    last_weekly_summary_day = ""
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

        # EOD rec extension: at market close, extend surviving rec expiries
        # to next market open so the dashboard stays populated overnight.
        if _ET is not None:
            now_et = datetime.now(_ET)
            if (today != last_eod_extend_day
                    and now_et.weekday() < 5
                    and now_et.hour == 16 and now_et.minute < 10):
                last_eod_extend_day = today
                try:
                    recs_on_disk = _load(RECS_FILE, [])
                    if isinstance(recs_on_disk, list) and recs_on_disk:
                        next_open = _next_market_open()
                        new_expiry = (next_open + timedelta(minutes=45)).isoformat()
                        for r in recs_on_disk:
                            r["expires_at"] = new_expiry
                        _save(RECS_FILE, recs_on_disk)
                        logger.info(
                            "EOD: extended %d recs to next market open (%s)",
                            len(recs_on_disk), next_open.strftime("%Y-%m-%d %H:%M UTC"),
                        )
                except Exception as exc:
                    logger.warning("EOD rec extension failed: %s", exc)

        # Weekly Telegram summary — every Monday at 8:00 AM ET
        if _ET is not None and _telegram is not None and _telegram.enabled:
            now_et = datetime.now(_ET)
            if (today != last_weekly_summary_day
                    and now_et.weekday() == 0       # Monday
                    and now_et.hour == 8 and now_et.minute < 10):
                last_weekly_summary_day = today
                try:
                    from datetime import timedelta as _td
                    week_ago = (datetime.utcnow() - _td(days=7)).isoformat()
                    history  = _load(HISTORY_FILE, [])
                    week_trades = [
                        t for t in (history if isinstance(history, list) else [])
                        if t.get("status") == "closed"
                        and (t.get("executed_at") or "") >= week_ago
                    ]
                    wins   = [t for t in week_trades if (t.get("pnl") or 0) > 0]
                    losses = [t for t in week_trades if (t.get("pnl") or 0) < 0]
                    total_pnl = sum(t.get("pnl") or 0 for t in week_trades)
                    best  = max(week_trades, key=lambda t: t.get("pnl") or 0, default={})
                    worst = min(week_trades, key=lambda t: t.get("pnl") or 0, default={})
                    asyncio.create_task(_telegram.send_weekly_summary({
                        "total_trades": len(week_trades),
                        "wins":         len(wins),
                        "losses":       len(losses),
                        "total_pnl":    total_pnl,
                        "best_trade":   {"ticker": best.get("ticker"), "pnl": best.get("pnl") or 0},
                        "worst_trade":  {"ticker": worst.get("ticker"), "pnl": worst.get("pnl") or 0},
                    }))
                except Exception as exc:
                    logger.warning("Weekly Telegram summary failed: %s", exc)

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

    task     = asyncio.create_task(_background_loop())
    trail    = asyncio.create_task(_trailing_stop_loop())
    eod_rev  = asyncio.create_task(_eod_position_review_loop())
    strat    = asyncio.create_task(_strategy_improvement_loop())
    autox    = asyncio.create_task(_auto_execute_loop())
    yield
    task.cancel()
    trail.cancel()
    eod_rev.cancel()
    strat.cancel()
    autox.cancel()
    for t in [task, trail, eod_rev, strat, autox]:
        try:
            await t
        except asyncio.CancelledError:
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


@app.get("/api/scan-results", dependencies=[Depends(_verify_bot_secret)])
def get_scan_results():
    data = _load(SCAN_LOG_FILE, {})
    if isinstance(data, dict):
        return {
            "picked":     data.get("picked", []),
            "rejected":   data.get("rejected", []),
            "scanned_at": data.get("scanned_at"),
        }
    return {"picked": [], "rejected": [], "scanned_at": None}


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


def _fetch_alpaca_fills_sync(page_size: int = 500) -> list:
    """Fetch FILL activities from Alpaca using the bot's own credentials (stdlib only)."""
    import urllib.request as _urlreq
    url = (
        f"{_BROKER_BASE}/v2/account/activities"
        f"?activity_type=FILL&page_size={page_size}&direction=asc"
    )
    req = _urlreq.Request(url, headers={
        "APCA-API-KEY-ID":     _ALPACA_KEY,
        "APCA-API-SECRET-KEY": _ALPACA_SECRET,
    })
    with _urlreq.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return data if isinstance(data, list) else []


def _win_rate_from_fills(fills: list) -> "tuple[float, int] | None":
    """FIFO win-rate from Alpaca fill activities.
    Returns (win_rate_pct, total_completed_trades) or None when no completed trades.
    """
    pos: dict = {}
    wins = total = 0
    EPS = 0.0001
    for f in fills:
        qty   = float(f.get("qty", 0) or 0)
        price = float(f.get("price", 0) or 0)
        sym   = f.get("symbol", "")
        side  = f.get("side", "")
        if not sym or not side or qty <= 0 or price <= 0:
            continue
        if sym not in pos:
            pos[sym] = {"qty": 0.0, "avg_cost": 0.0, "pnl": 0.0}
        p = pos[sym]
        if side == "buy":
            if p["qty"] < 0:
                cover = min(qty, -p["qty"])
                p["pnl"] += (p["avg_cost"] - price) * cover
                p["qty"] += cover
                if abs(p["qty"]) < EPS:
                    total += 1
                    if p["pnl"] > 0:
                        wins += 1
                    p.update({"qty": 0.0, "avg_cost": 0.0, "pnl": 0.0})
                rem = qty - cover
                if rem > EPS:
                    p.update({"qty": rem, "avg_cost": price, "pnl": 0.0})
            else:
                new_qty = p["qty"] + qty
                p["avg_cost"] = (p["avg_cost"] * p["qty"] + price * qty) / new_qty
                p["qty"] = new_qty
        else:
            if p["qty"] > 0:
                sell = min(qty, p["qty"])
                p["pnl"] += (price - p["avg_cost"]) * sell
                p["qty"] -= sell
                if abs(p["qty"]) < EPS:
                    total += 1
                    if p["pnl"] > 0:
                        wins += 1
                    p.update({"qty": 0.0, "avg_cost": 0.0, "pnl": 0.0})
                rem = qty - sell
                if rem > EPS:
                    p.update({"qty": -rem, "avg_cost": price, "pnl": 0.0})
            else:
                new_qty = -p["qty"] + qty
                p["avg_cost"] = (p["avg_cost"] * (-p["qty"]) + price * qty) / new_qty
                p["qty"] -= qty
    return (round(wins / total * 100, 1), total) if total > 0 else None


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

    # Sharpe: daily returns (PnL / entry_value) bucketed by close day, annualised.
    # Using returns (%) rather than raw dollars makes this scale-invariant.
    import numpy as _np
    from collections import defaultdict as _dd
    _daily_ret: dict = _dd(float)
    _daily_val: dict = _dd(float)
    for t in closed:
        day = str(t.get("closed_at", t.get("executed_at", "")))[:10]
        if not day:
            continue
        pnl_val   = float(t.get("pnl") or t.get("realized_pnl") or 0)
        entry_val = float(t.get("entry") or 1) * int(t.get("qty") or 1)
        _daily_ret[day] += pnl_val
        _daily_val[day] += entry_val
    sharpe = 0.0
    # Build daily return % series
    ret_series = []
    for day in _daily_ret:
        basis = _daily_val.get(day, 0)
        if basis > 0:
            ret_series.append(_daily_ret[day] / basis)
    if len(ret_series) >= 2:
        arr = _np.array(ret_series)
        std = float(_np.std(arr, ddof=1))
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

    # Override win_rate with Alpaca-sourced FIFO computation when available.
    # The local HISTORY_FILE is empty on Railway's ephemeral filesystem, so the
    # local win_rate is always 0 there. The fills-based computation uses the
    # bot's own credentials — guaranteed to match the account it actually trades on.
    if _ALPACA_KEY and _ALPACA_SECRET:
        try:
            fills   = _fetch_alpaca_fills_sync()
            result  = _win_rate_from_fills(fills)
            if result is not None:
                win_rate, fills_total = result
                if not total_trades:
                    total_trades = fills_total
        except Exception as _exc:
            logger.warning("fill-based win rate unavailable: %s", _exc)

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
        "timestamp":   datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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
    ticker:            str = Field(..., min_length=1, max_length=10, pattern=r"^[A-Z0-9.\-]+$")
    direction:         Literal["LONG", "SHORT"]
    qty:               int = Field(..., gt=0)
    entry:             float = Field(..., gt=0)
    stop_loss:         float = Field(..., gt=0)
    take_profit:       float = Field(..., gt=0)
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


def _entry_guard_reason(ticker: str, direction: str,
                        composite_score: Optional[float], beta: Optional[float],
                        history: list) -> Optional[str]:
    """Shared pre-trade risk gates. Returns a rejection reason (and logs it) when
    a trade must be blocked, or None when it is clear to enter.

    Used by BOTH /api/execute and the autonomous executor so manual and auto
    entries honour the exact same circuit-breaker / position / sector / beta caps.
    """
    cb_reason = _check_circuit_breaker()
    if cb_reason:
        _log_rejection(ticker, "circuit_breaker", composite_score or 0.0,
                       {"circuit_breaker_reason": cb_reason})
        return cb_reason

    open_count = len([t for t in history if t.get("status") == "open"])
    if open_count >= MAX_OPEN_POSITIONS:
        _log_rejection(ticker, "max_positions", composite_score or 0.0,
                       {"open_count": open_count, "max": MAX_OPEN_POSITIONS})
        return f"Max open positions ({MAX_OPEN_POSITIONS}) reached"

    # Sector correlation guard: max 2 open positions per sector
    ticker_sector = _SECTOR_MAP.get(ticker.upper(), "Other")
    open_in_sector = sum(
        1 for t in history
        if t.get("status") == "open"
        and _SECTOR_MAP.get(t.get("ticker", "").upper(), "Other") == ticker_sector
    )
    if open_in_sector >= 2:
        return f"Sector limit: {open_in_sector} open positions in {ticker_sector}"

    # Portfolio beta cap: net |beta| across all open positions
    portfolio_beta = sum(
        float(t.get("beta", 1.0)) * (1.0 if t.get("direction") == "LONG" else -1.0)
        for t in history if t.get("status") == "open"
    )
    new_beta = float(beta or 1.0) * (1.0 if direction == "LONG" else -1.0)
    if abs(portfolio_beta + new_beta) > PORTFOLIO_BETA_CAP:
        _log_rejection(ticker, "beta_cap", composite_score or 0.0,
                       {"portfolio_beta": round(portfolio_beta, 2),
                        "new_beta": round(new_beta, 2),
                        "cap": PORTFOLIO_BETA_CAP})
        return (f"Portfolio beta cap: net beta {portfolio_beta + new_beta:+.2f} "
                f"would exceed ±{PORTFOLIO_BETA_CAP}")
    return None


async def _record_executed_trade(body: ExecuteBody) -> tuple[Optional[str], Optional[dict]]:
    """Atomically guard-check and record a trade. Returns (reject_reason, None)
    when a gate blocks it, or (None, trade) when recorded.

    Guard + append happen under one lock so concurrent entries can't both slip
    past the position/sector caps. Assumes any real broker order was already
    placed by the caller (the dashboard or the auto-executor)."""
    async with _trades_lock:
        history = _load(HISTORY_FILE, [])
        if not isinstance(history, list):
            history = []
        reason = _entry_guard_reason(body.ticker, body.direction,
                                     body.composite_score, body.beta, history)
        if reason:
            return reason, None

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
            "executed_at":     datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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
    return None, trade


@app.post("/api/execute", dependencies=[Depends(_verify_bot_secret)])
async def execute_trade(body: ExecuteBody):
    reason, trade = await _record_executed_trade(body)
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    if _telegram is not None and trade:
        asyncio.create_task(_telegram.send_trade_entry(trade))
    return {"status": "recorded", "trade_id": trade["id"]}


# ---------------------------------------------------------------------------
# Autonomous paper executor (Railway) — places entries by itself when armed.
# ---------------------------------------------------------------------------

def _auto_exec_disarmed_reason() -> Optional[str]:
    """Why the autonomous executor may NOT place orders, or None when armed.

    ALL of these must hold, so it can never trade a live account or fire by
    accident: the deploy opt-in, a paper account, real keys, and the dashboard
    toggle the operator already uses for the PC bot."""
    if not AUTO_EXECUTE_ON_RAILWAY:
        return "AUTO_EXECUTE_ON_RAILWAY off"
    if not _ALPACA_PAPER:
        return "ALPACA_PAPER is false — refusing to auto-trade a non-paper account"
    if not (_ALPACA_KEY and _ALPACA_SECRET):
        return "Alpaca API keys not set"
    if not _load_trade_mode().get("auto_execute", False):
        return "dashboard auto-execute toggle off"
    return None


def _auto_exec_candidates(recs: list, now_iso: str) -> list:
    """Strong, fresh, sized recommendations eligible for autonomous entry.

    Conviction is symmetric: a LONG needs composite_score >= AUTO_EXEC_MIN_SCORE,
    a SHORT needs it <= 100 - AUTO_EXEC_MIN_SCORE. Expired or unsizable (qty<=0)
    recs are skipped."""
    out: list = []
    for r in recs:
        if not isinstance(r, dict):
            continue
        direction = r.get("direction")
        score = r.get("composite_score")
        if score is None or direction not in ("LONG", "SHORT"):
            continue
        strong = (score >= AUTO_EXEC_MIN_SCORE) if direction == "LONG" \
            else (score <= 100 - AUTO_EXEC_MIN_SCORE)
        if not strong:
            continue
        exp = r.get("expires_at")
        if exp and str(exp) <= now_iso:
            continue
        if int((r.get("risk") or {}).get("qty") or 0) <= 0:
            continue
        out.append(r)
    return out


async def _submit_paper_bracket(session: aiohttp.ClientSession, *, ticker: str,
                                direction: str, qty: int, stop_loss: float,
                                take_profit: float) -> Optional[str]:
    """Submit a paper bracket order to Alpaca. Returns the order_id or None.

    Mirrors the shape used by the PC broker and the dashboard (market entry,
    TIF day, bracket children) so fills behave identically across venues."""
    direction = str(direction).upper()
    if direction not in ("LONG", "SHORT"):
        logger.error("_submit_paper_bracket: invalid direction %r for %s — skipping", direction, ticker)
        return None
    side = "buy" if direction == "LONG" else "sell"
    # Bracket sanity: for LONG SL must be below TP; for SHORT SL must be above TP.
    bracket_ok = (stop_loss < take_profit) if side == "buy" else (stop_loss > take_profit)
    if not bracket_ok:
        logger.error("_submit_paper_bracket: bracket legs inverted for %s %s SL=%.4f TP=%.4f — skipping",
                     direction, ticker, stop_loss, take_profit)
        return None
    order = {
        "symbol":        ticker,
        "qty":           str(int(qty)),
        "side":          side,
        "type":          "market",
        "time_in_force": "day",
        "order_class":   "bracket",
        "stop_loss":     {"stop_price":  str(round(stop_loss, 2))},
        "take_profit":   {"limit_price": str(round(take_profit, 2))},
    }
    try:
        async with session.post(f"{_BROKER_BASE}/v2/orders", headers=_ALPACA_HEADERS,
                                json=order, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status in (200, 201):
                data = await r.json()
                return data.get("id")
            logger.warning("Auto-exec: Alpaca rejected %s %s: HTTP %s %s",
                           direction, ticker, r.status, (await r.text())[:200])
    except Exception as exc:
        logger.warning("Auto-exec: submit failed for %s: %s", ticker, exc)
    return None


async def _run_auto_executor() -> int:
    """One sweep: place paper orders for eligible recs, return how many placed."""
    recs = _load(RECS_FILE, [])
    if not isinstance(recs, list):
        return 0
    candidates = _auto_exec_candidates(recs, datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
    if not candidates:
        return 0

    placed = 0
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
    ) as session:
        for rec in candidates:
            ticker    = str(rec.get("ticker", "")).upper()
            direction = rec["direction"]
            risk      = rec.get("risk") or {}
            qty       = int(risk.get("qty") or 0)

            # Re-read history each iteration so it reflects orders just placed.
            history = _load(TRADES_FILE, [])
            history = history if isinstance(history, list) else []
            if any(t.get("status") == "open"
                   and str(t.get("ticker", "")).upper() == ticker
                   and t.get("direction") == direction
                   for t in history):
                continue  # already holding this name+side

            # Pre-check guards BEFORE placing, so we never orphan an order.
            reason = _entry_guard_reason(ticker, direction, rec.get("composite_score"),
                                         rec.get("beta"), history)
            if reason:
                logger.info("Auto-exec skip %s %s: %s", direction, ticker, reason)
                continue

            order_id = await _submit_paper_bracket(
                session, ticker=ticker, direction=direction, qty=qty,
                stop_loss=float(risk["stop_loss"]), take_profit=float(risk["take_profit"]),
            )
            if not order_id:
                continue

            body = ExecuteBody(
                ticker=ticker, direction=direction, qty=qty,
                entry=float(risk.get("entry") or 0),
                stop_loss=float(risk["stop_loss"]), take_profit=float(risk["take_profit"]),
                recommendation_id=rec.get("id"), order_id=order_id,
                composite_score=rec.get("composite_score"),
                evaluations=rec.get("evaluations"), beta=rec.get("beta"),
            )
            rej, _ = await _record_executed_trade(body)
            if rej:
                logger.warning("Auto-exec: order %s placed for %s but record rejected "
                               "(%s) — orphaned bracket", order_id, ticker, rej)
                continue
            placed += 1
            logger.info("Auto-exec PLACED %s %s x%d @ market (order %s, score %.1f)",
                        direction, ticker, qty, order_id, rec.get("composite_score") or 0.0)
    return placed


async def _auto_execute_loop() -> None:
    """Autonomous paper-entry loop. OFF unless explicitly armed.

    Sweeps the latest recommendations every AUTO_EXEC_POLL_MIN minutes during
    market hours and submits Alpaca PAPER bracket orders for strong, fresh,
    sized signals — applying the SAME guards as /api/execute. Exits are then
    handled by the existing close/trailing/EOD loops."""
    await asyncio.sleep(20)  # let the startup scan populate recommendations
    interval = max(60, AUTO_EXEC_POLL_MIN * 60)
    last_reason = ""
    while True:
        await asyncio.sleep(interval)
        reason = _auto_exec_disarmed_reason()
        if reason:
            if reason != last_reason:
                logger.info("Auto-executor disarmed: %s", reason)
                last_reason = reason
            continue
        last_reason = ""
        if not _is_market_open():
            continue
        try:
            placed = await _run_auto_executor()
            if placed:
                logger.info("Auto-executor: placed %d paper order(s) this sweep", placed)
        except Exception as exc:
            logger.warning("Auto-executor error: %s", exc)


@app.post("/api/scan", dependencies=[Depends(_verify_bot_secret)])
async def trigger_scan():
    if _scan_stats.get("running"):
        return {"status": "already_running", "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}
    asyncio.create_task(_run_market_scan(force=True))
    return {"status": "scan_triggered", "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}


@app.post("/api/reset-circuit-breaker", dependencies=[Depends(_verify_bot_secret)])
async def reset_circuit_breaker():
    """Manually reset the circuit breaker after reviewing losses."""
    _circuit_breaker.update({
        "halted":    False,
        "reason":    None,
        "halted_at": None,
    })
    logger.info("Circuit breaker manually reset")
    return {"status": "reset", "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}


@app.get("/api/rejections", dependencies=[Depends(_verify_bot_secret)])
def get_rejections(limit: int = 50):
    """Return the last `limit` trade rejection records."""
    limit = max(1, min(limit, 500))
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
    days = max(1, min(days, 365))
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
    """Return backtest_results.json and backtest_optimal.json (volume or repo root)."""
    base = _VOLUME or _REPO_ROOT
    def read_json(name: str):
        try:
            return json.loads((base / name).read_text())
        except Exception:
            return None

    def read_text(name: str):
        try:
            return (base / name).read_text()
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
    if _backtest_stats.get("running"):
        return {"status": "already_running", "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}
    asyncio.create_task(_run_backtest())
    return {"status": "backtest_triggered", "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}


@app.post("/api/optimize/run", dependencies=[Depends(_verify_bot_secret)])
async def run_optimizer_now():
    """Trigger the walk-forward profit optimizer (writes backtest_optimal.json)."""
    if _optimizer_stats.get("running"):
        return {"status": "already_running", "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}
    asyncio.create_task(_run_optimizer())
    return {"status": "optimizer_triggered", "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}


@app.get("/api/backtest/log", dependencies=[Depends(_verify_bot_secret)])
def get_backtest_log():
    """Return live or completed backtest log lines as JSON."""
    from fastapi.responses import JSONResponse
    running = bool(_backtest_stats.get("log_lines") and not _backtest_stats.get("last_log"))
    lines: list[str] = list(_backtest_stats.get("log_lines") or [])
    last_log: str = _backtest_stats.get("last_log") or ""
    return JSONResponse({
        "running": running,
        "lines":   lines if (running or not last_log) else last_log.splitlines(),
        "status":  _backtest_stats.get("last_status"),
    })


@app.get("/api/optimize/log", dependencies=[Depends(_verify_bot_secret)])
def get_optimizer_log():
    """Return live or completed optimizer log lines."""
    from fastapi.responses import JSONResponse
    running = bool(_optimizer_stats.get("running"))
    lines: list[str] = list(_optimizer_stats.get("log_lines") or [])
    last_log: str = _optimizer_stats.get("last_log") or ""
    return JSONResponse({
        "running": running,
        "lines":   lines if running else (last_log.splitlines() if last_log else []),
        "status":  _optimizer_stats.get("last_status"),
    })


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
    weights["applied_from_optimizer_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    weights["applied_oos_pnl"]           = round(float(oos_pnl), 2)
    weights["live_tuning_active"]        = True   # let the live bot honor these params
    _save_weights(weights)

    logger.info("Applied optimizer params to live config: %s (OOS PnL=$%.0f)", applied, oos_pnl)
    return {
        "status":    "applied",
        "applied":   applied,
        "oos_pnl":   round(float(oos_pnl), 2),
        "validated": validated,
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }


@app.post("/api/optimize/reset", dependencies=[Depends(_verify_bot_secret)])
def reset_strategy_weights():
    """Reset strategy_weights.json to factory defaults and clear manual overrides."""
    reset = dict(DEFAULT_WEIGHTS)
    reset["manual_overrides"] = {}
    _save_weights(reset)
    logger.info("Strategy weights reset to defaults by operator")
    return {"status": "reset", "weights": reset}


class PatchWeightsBody(BaseModel):
    # float value → set + lock that field; null → unlock (self-tuner takes over)
    min_score:           Optional[float] = None
    atr_stop_multiple:   Optional[float] = None
    atr_target_multiple: Optional[float] = None
    time_window_minutes: Optional[float] = None
    # Explicit null sentinel — FastAPI doesn't distinguish "omitted" from "null"
    # without this; we use a separate flag dict instead.
    unlock:              Optional[List[str]] = None


@app.patch("/api/optimize/patch", dependencies=[Depends(_verify_bot_secret)])
def patch_strategy_weights(body: PatchWeightsBody):
    """Apply user-defined overrides to strategy weights.

    - Non-null field → write value AND lock it (self-tuner will skip it).
    - Field listed in `unlock` → remove its manual lock (self-tuner resumes).
    - Omitted fields → untouched.
    """
    weights   = _load_weights()
    overrides = dict(weights.get("manual_overrides") or {})
    updated:  Dict[str, float] = {}
    unlocked: List[str]        = []

    fields = {
        "min_score":           body.min_score,
        "atr_stop_multiple":   body.atr_stop_multiple,
        "atr_target_multiple": body.atr_target_multiple,
        "time_window_minutes": body.time_window_minutes,
    }
    for key, val in fields.items():
        if val is not None:
            weights[key]   = float(val)
            overrides[key] = True
            updated[key]   = float(val)

    for key in (body.unlock or []):
        if key in overrides:
            del overrides[key]
            unlocked.append(key)

    # Write the new values as the baseline — no locks, no expiry.
    # The self-tuner will continue adjusting from these values on the next update cycle.
    _save_weights(weights)
    logger.info("Strategy weights seeded by operator: %s", updated)
    return {"status": "ok", "updated": updated}


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


@app.get("/api/optimize/weights", dependencies=[Depends(_verify_bot_secret)])
def get_strategy_weights():
    """Return all current strategy weights including self-tuner stats and manual overrides."""
    w = _load_weights()
    return {
        "min_score":           w.get("min_score",           DEFAULT_WEIGHTS["min_score"]),
        "atr_stop_multiple":   w.get("atr_stop_multiple",   DEFAULT_WEIGHTS["atr_stop_multiple"]),
        "atr_target_multiple": w.get("atr_target_multiple", DEFAULT_WEIGHTS["atr_target_multiple"]),
        "time_window_minutes": w.get("time_window_minutes", DEFAULT_WEIGHTS["time_window_minutes"]),
        "win_rate_30d":        w.get("win_rate_30d"),
        "update_count":        w.get("update_count", 0),
        "bias":                w.get("bias", "neutral"),
        "last_updated":        w.get("last_updated"),
        "manual_overrides":    w.get("manual_overrides") or {},
        "override_expires_at": w.get("override_expires_at"),
        "defaults": {
            "min_score":           DEFAULT_WEIGHTS["min_score"],
            "atr_stop_multiple":   DEFAULT_WEIGHTS["atr_stop_multiple"],
            "atr_target_multiple": DEFAULT_WEIGHTS["atr_target_multiple"],
            "time_window_minutes": DEFAULT_WEIGHTS["time_window_minutes"],
        },
    }


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


class TradeModeBody(BaseModel):
    auto_execute: bool


@app.get("/api/trade-mode", dependencies=[Depends(_verify_bot_secret)])
def get_trade_mode():
    """Return the current execution mode (auto-execute vs manual approval)."""
    return _load_trade_mode()


@app.post("/api/trade-mode", dependencies=[Depends(_verify_bot_secret)])
def set_trade_mode(body: TradeModeBody):
    """Toggle whether the bot auto-executes entries or waits for manual approval.

    Written to data/trade_mode.json; live_runner reads it each evaluation
    cycle, so the switch takes effect within one scan without a redeploy.
    """
    _save(TRADE_MODE_FILE, {
        "auto_execute": body.auto_execute,
        "updated_at":   datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    })
    logger.info("Trade mode set: auto_execute=%s", body.auto_execute)
    return {"status": "ok", "auto_execute": body.auto_execute}


class BrokerModeBody(BaseModel):
    broker: str


@app.get("/api/broker-mode", dependencies=[Depends(_verify_bot_secret)])
def get_broker_mode():
    """Return the currently-selected execution broker (alpaca | ibkr)."""
    return _load_broker_mode()


@app.post("/api/broker-mode", dependencies=[Depends(_verify_bot_secret)])
def set_broker_mode(body: BrokerModeBody):
    """Switch the execution broker between Alpaca and IBKR.

    Written to data/broker_mode.json; live_runner polls it and restarts its
    trading session on the new broker (flattening open positions first when
    auto-execute is live, so positions are never orphaned across venues).
    """
    choice = (body.broker or "").lower()
    if choice not in ("alpaca", "ibkr"):
        raise HTTPException(status_code=400, detail="broker must be 'alpaca' or 'ibkr'")
    _save(BROKER_MODE_FILE, {
        "broker":     choice,
        "updated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    })
    logger.info("Broker mode set: %s", choice)
    return {"status": "ok", "broker": choice}


@app.get("/api/scorecard", dependencies=[Depends(_verify_bot_secret)])
def get_scorecard():
    """Edge metrics + an honest confidence flag over the live paper track record."""
    try:
        from core.scorecard import build_scorecard
        return build_scorecard().as_dict()
    except Exception as exc:
        logger.warning("scorecard failed: %s", exc)
        return {"trades": 0, "confidence": "insufficient", "verdict": "scorecard unavailable"}


@app.get("/api/health")
def health():
    gemini_set    = bool(os.getenv("GEMINI_API_KEY"))
    anthropic_set = bool(os.getenv("ANTHROPIC_API_KEY"))
    execute_live  = os.getenv("EXECUTE_LIVE", "false").lower() in ("1", "true", "yes")
    alpaca_paper  = os.getenv("ALPACA_PAPER", "true").lower() not in ("0", "false", "no")
    auto_execute  = _load_trade_mode()["auto_execute"]
    return {
        "status":    "ok",
        "agents":    _AGENTS_AVAILABLE,
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "backtest":  _backtest_stats,
        "optimizer": _optimizer_stats,
        "circuit_breaker": _circuit_breaker,
        "trading": {
            "execute_live": execute_live,
            "auto_execute": auto_execute,
            "paper_mode":   alpaca_paper,
            "broker":       _load_broker_mode()["broker"],
            "mode_label":   (
                "DRY RUN"                                    if not execute_live else
                ("AUTO · PAPER" if alpaca_paper else "AUTO · LIVE") if auto_execute else
                "MANUAL"
            ),
        },
        "keys": {
            "gemini":    gemini_set,
            "anthropic": anthropic_set,
            "vision_ready": gemini_set or anthropic_set,
        },
        "issues": _health_issues(),
    }


def _health_issues() -> list:
    """Actionable issues the operator needs to fix (rejected key, no equity, …)."""
    try:
        from core import health
        return [
            {
                "key":         i.key,
                "message":     i.message,
                "remediation": i.remediation,
                "severity":    i.severity,
                "count":       i.count,
            }
            for i in health.active_issues()
        ]
    except Exception:
        return []


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


@app.get("/api/learning", dependencies=[Depends(_verify_bot_secret)])
def get_learning():
    """Online-learning view: how the WeightTuner has adapted agent weights.

    Returns the time series of tuning snapshots (agent weights, multipliers,
    win rate, thresholds) plus the latest values, so the dashboard can render
    the bot learning from its own track record. Empty/quiet until the tuner has
    enough resolved trades (see weight_tuner._MIN_TRADES).
    """
    history: List[Dict[str, Any]] = []
    try:
        if LEARNING_HISTORY_FILE.exists():
            for line in LEARNING_HISTORY_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    history.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        logger.debug("learning history read failed", exc_info=True)

    current = _load(LEARNING_WEIGHTS_FILE, {})
    if not isinstance(current, dict):
        current = {}

    latest = history[-1] if history else {}
    return {
        "active":     bool(current.get("live_tuning_active")),
        "history":    history,
        "weights":    current.get("agent_weights")     or latest.get("weights", {}),
        "multipliers": current.get("agent_multipliers") or latest.get("multipliers", {}),
        "win_rate":   current.get("win_rate_30d", latest.get("win_rate")),
        "long_win_rate":  current.get("long_win_rate",  latest.get("long_win_rate")),
        "short_win_rate": current.get("short_win_rate", latest.get("short_win_rate")),
        "bias":       current.get("bias", latest.get("bias", "neutral")),
        "long_threshold":  current.get("long_threshold",  latest.get("long_threshold")),
        "short_threshold": current.get("short_threshold", latest.get("short_threshold")),
        "sample_size": current.get("sample_size", latest.get("sample_size", 0)),
        "steps":      len(history),
        "simulated":  bool(current.get("simulated") or latest.get("simulated")),
    }


@app.get("/api/agent-scorecards", dependencies=[Depends(_verify_bot_secret)])
def get_agent_scorecards():
    """Per-agent track record maintained by the strategy-improvement loop.

    Each entry carries the agent's directional hit rate and sample size over the
    tuner's rolling window, plus the live weight/multiplier in force. Recomputes
    on demand if the loop hasn't written the file yet (e.g. fresh process)."""
    data = _load(AGENT_SCORECARDS_FILE, None)
    if not isinstance(data, dict):
        return {
            "updated_at":    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "sample_trades": 0,
            "agents":        _refresh_agent_scorecards(),
        }
    return data


@app.post("/api/learning/simulate", dependencies=[Depends(_verify_bot_secret)])
def post_learning_simulate(n_trades: int = 140):
    """Seed the Learning view by driving the real WeightTuner over a simulated
    track record. Clearly tagged simulated=true; superseded by real tuning steps.
    """
    n_trades = max(20, min(n_trades, 500))
    try:
        from simulate_learning import run_simulation
        result = run_simulation(n_trades=n_trades)
    except Exception as exc:
        logger.exception("learning simulation failed")
        raise HTTPException(status_code=500, detail=f"simulation failed: {exc}")
    return {**result, **get_learning()}


@app.get("/api/monte-carlo", dependencies=[Depends(_verify_bot_secret)])
def get_monte_carlo(n_sims: int = 10_000):
    """Monte Carlo resample of trade win/loss sequence → 95% CI on win rate and PnL."""
    n_sims = max(100, min(n_sims, 100_000))
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


@app.get("/api/validation", dependencies=[Depends(_verify_bot_secret)])
def get_validation():
    """Statistical edge audit on the REALISED track record (Pillar 3).

    Per-trade returns + a sign-flip randomization significance test + the equity
    and underwater-drawdown series for the dashboard to plot. Pure numpy/pandas,
    so it runs on Railway with no extra deps. Heavy price-permutation / candlestick
    rendering stays in the offline CLI (validation.run)."""
    try:
        from validation.trade_history import analyze
        res = analyze(TRADES_FILE, n_perm=1000, seed=7)
    except Exception as exc:
        logger.debug("validation failed", exc_info=True)
        return {"trades": 0, "message": f"validation unavailable: {exc}"}
    p = (res.get("randomization_test") or {}).get("p_value")
    res["verdict"] = (
        "edge" if (p is not None and p < 0.01)
        else "weak" if (p is not None and p < 0.05)
        else "inconclusive"
    )
    return res


@app.get("/api/exit-decisions", dependencies=[Depends(_verify_bot_secret)])
def get_exit_decisions():
    """Rolling log of exit-monitor and EOD review decisions (newest first)."""
    return list(reversed(_EXIT_DECISIONS))



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=False)
