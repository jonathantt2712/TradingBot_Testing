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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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

try:
    import pandas as pd
    import numpy as np
    from bootstrap import build_manager
    from config.settings import load_settings
    from core.models import AnalysisContext
    from core.enums import Decision
    from data.ai4trade_client import AI4TradeClient as _AI4TC

    _ai4trade_client = _AI4TC(
        email=os.getenv("AI4TRADE_EMAIL", ""),
        password=os.getenv("AI4TRADE_PASSWORD", ""),
    )
    _pm = build_manager(load_settings(), broker=None, ai4=_ai4trade_client)
    # Dashboard scans fetch ~100-bar windows (vs 200 live) — keep the lower
    # bar requirement this endpoint has always used.
    _pm.technical.min_bars = 30
    _Decision = Decision
    _AGENTS_AVAILABLE = True
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


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


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
    except Exception as exc:
        logger.error("Could not fetch account equity — recs will be unsized: %s", exc)
    return 0.0


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
            logger.info("Closed %s %s via %s: exit=%.2f PnL=$%.2f (%.2f%%)",
                        direction, trade["ticker"], exit_reason, exit_price, pnl, pnl_pct)

        except Exception as exc:
            logger.debug("Could not check order %s: %s", trade.get("order_id"), exc)

    if modified:
        _save(TRADES_FILE, trades)


# === Strategy weight learning ===

def _update_strategy_weights() -> None:
    trades  = _load(TRADES_FILE, [])
    weights = _load_weights()

    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None]
    recent = closed[-20:]
    if len(recent) < 5:
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
                f"{_DATA_BASE}/v1beta1/screener/stocks/most-actives?by=volume&top=25",
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
            ][:20]

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

        if spy_chg > 0.5 and qqq_chg > 0.5 and vix_approx < 25:
            regime_label = "risk_on"
            regime_rationale = f"SPY +{spy_chg:.2f}%, QQQ +{qqq_chg:.2f}%, VIX-proxy {vix_approx:.1f} — bullish"
        elif spy_chg < -0.5 or vix_approx > 35:
            regime_label = "risk_off"
            regime_rationale = f"SPY {spy_chg:.2f}%, VIX-proxy {vix_approx:.1f} — bearish"
        elif abs(spy_chg) < 0.3 and abs(qqq_chg) < 0.3:
            regime_label = "choppy"
            regime_rationale = f"SPY {spy_chg:.2f}%, QQQ {qqq_chg:.2f}% — low momentum"
        else:
            regime_label = "neutral"
            regime_rationale = f"SPY {spy_chg:.2f}%, QQQ {qqq_chg:.2f}%"

        _save(REGIME_FILE, {
            "regime":      regime_label,
            "vix_level":   vix_approx if vix_approx > 0 else 15.0,
            "spy_day_chg": spy_chg,
            "qqq_day_chg": qqq_chg,
            "rationale":   regime_rationale,
            "timestamp":   datetime.utcnow().isoformat(),
        })

        recs: List[Dict[str, Any]] = []

        for sym in symbols_raw:
            snap = snaps.get(sym) or {}
            if not snap:
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
                ctx      = AnalysisContext(ticker=sym, bars=df, account={"equity": equity})
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

async def _background_loop() -> None:
    await asyncio.sleep(5)
    await _run_market_scan()
    consecutive_errors = 0
    last_day = ""
    while True:
        # Reset daily scan stats at midnight
        today = str(date.today())
        if today != last_day:
            _reset_scan_stats_if_needed()
            last_day = today

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

    task = asyncio.create_task(_background_loop())
    yield
    task.cancel()
    try:
        await task
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

@app.get("/api/recommendations")
def get_recommendations():
    data = _load(RECS_FILE, [])
    if isinstance(data, list):
        return data
    return []


@app.get("/api/history")
def get_history():
    data = _load(HISTORY_FILE, [])
    if isinstance(data, list):
        return data
    return []


@app.get("/api/pnl")
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


@app.get("/api/stats")
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



@app.get("/api/regime")
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


@app.get("/api/sectors")
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


@app.get("/api/open")
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


@app.post("/api/execute")
def execute_trade(body: ExecuteBody):
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
    }
    history = _load(HISTORY_FILE, [])
    if not isinstance(history, list):
        history = []
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


@app.post("/api/scan")
async def trigger_scan():
    asyncio.create_task(_run_market_scan())
    return {"status": "scan_triggered", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/scan-stats")
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
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "agents": _AGENTS_AVAILABLE, "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=False)
