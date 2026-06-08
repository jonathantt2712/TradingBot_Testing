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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from dotenv import load_dotenv
load_dotenv()

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
_tech_agent = None
_risk_agent = None
_Decision = None

try:
    import pandas as pd
    import numpy as np
    from agents.technical_agent import TechnicalAgent
    from agents.risk_agent import RiskAgent
    from config.settings import RiskConfig
    from core.models import AnalysisContext
    from core.enums import Decision

    _tech_agent = TechnicalAgent(weight=1.0, min_bars=30)
    _risk_cfg   = RiskConfig()
    _risk_agent = RiskAgent(_risk_cfg, weight=0.0)
    _Decision   = Decision
    _AGENTS_AVAILABLE = True
    logger.info("Agent pipeline loaded -- TechnicalAgent + RiskAgent active")
except Exception as _import_err:
    logger.warning("Agent imports failed (%s) -- scanner using fallback formula", _import_err)


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
WEIGHTS_FILE = DATA_DIR / "strategy_weights.json"


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
    risk_per_share   = abs(entry - stop_loss)
    reward_per_share = abs(take_profit - entry)
    if risk_per_share < 0.0001:
        return max(1, int(2000 / max(entry, 1)))

    b = reward_per_share / risk_per_share
    p = min(max(composite_score / 100.0, 0.05), 0.95)
    q = 1.0 - p
    kelly_f    = (b * p - q) / b if b > 0 else 0.0
    half_kelly = max(kelly_f / 2, 0.0)

    base_risk   = 0.01 * equity
    scaled_risk = base_risk * (half_kelly / 0.25)
    qty         = int(scaled_risk / risk_per_share)
    max_qty     = int((0.15 * equity) / max(entry, 1))
    return max(1, min(qty, max_qty))


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
    try:
        async with session.get(
            f"{_BROKER_BASE}/v2/account",
            headers=_ALPACA_HEADERS,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            if r.status == 200:
                data = await r.json()
                return float(data.get("equity") or data.get("cash") or 10_000)
    except Exception as exc:
        logger.warning("Could not fetch account equity: %s", exc)
    return 10_000.0


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
    if not _AGENTS_AVAILABLE or not symbols:
        return {}
    start = (datetime.utcnow() - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        async with session.get(
            f"{_DATA_BASE}/v2/stocks/bars",
            params={"symbols": ",".join(symbols), "timeframe": timeframe,
                    "start": start, "limit": str(limit), "feed": "iex"},
            headers=_ALPACA_HEADERS,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status != 200:
                logger.warning("Multi-bars fetch returned %s", r.status)
                return {}
            payload = await r.json()
    except Exception as exc:
        logger.warning("Multi-bars fetch failed: %s", exc)
        return {}

    result: Dict[str, Any] = {}
    for sym, bars_list in (payload.get("bars") or {}).items():
        df = _bars_to_df(bars_list)
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
            order_id = trade["order_id"]
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


async def _run_market_scan() -> None:
    if not _ALPACA_KEY or not _ALPACA_SECRET:
        logger.warning("Alpaca credentials missing -- skipping auto-scan")
        return

    weights     = _load_weights()
    min_chg     = weights.get("min_chg_pct",        0.3)
    stop_pct    = weights.get("stop_pct",            0.02)
    tp_pct      = weights.get("tp_pct",              0.05)
    score_floor = weights.get("score_floor",         20)
    score_ceil  = weights.get("score_ceil",          80)
    min_score   = weights.get("min_score",           40)
    win_mins    = weights.get("time_window_minutes", 45)

    if _AGENTS_AVAILABLE and _risk_agent is not None:
        _risk_agent.cfg.atr_stop_multiple   = weights.get("atr_stop_multiple",   2.0)
        _risk_agent.cfg.atr_target_multiple = weights.get("atr_target_multiple", 3.0)

    try:
        async with aiohttp.ClientSession() as session:
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

            bars_map = await _fetch_multi_bars(session, list(set(symbols_raw + ["SPY"])), limit=100)

        if _AGENTS_AVAILABLE and _tech_agent is not None:
            _tech_agent.spy_bars = bars_map.get("SPY")

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

            if _AGENTS_AVAILABLE and _tech_agent is not None and df is not None and len(df) >= 20:
                ctx         = AnalysisContext(ticker=sym, bars=df, account={"equity": equity})
                eval_result = await _tech_agent.safe_evaluate(ctx)
                score       = float(eval_result.score)
                rationale   = eval_result.rationale or ""

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
                plan       = _risk_agent.build_plan(ctx, intended=intended)

                if plan is not None and plan.risk_reward >= 1.0:
                    entry       = round(plan.entry, 2)
                    stop_loss   = round(plan.stop_loss, 2)
                    take_profit = round(plan.take_profit, 2)
                    qty         = max(1, int(plan.qty))
                    rr          = round(plan.risk_reward, 2)
                else:
                    entry = round(price, 2)
                    d     = 1 if direction == "LONG" else -1
                    stop_loss   = round(entry * (1 - d * stop_pct), 2)
                    take_profit = round(entry * (1 + d * tp_pct),   2)
                    qty  = _kelly_qty(equity, entry, stop_loss, take_profit, score)
                    rr   = round(tp_pct / stop_pct, 2)

            else:
                chg_w = weights.get("chg_weight", 4.0)
                intra_w = weights.get("intra_weight", 2.0)
                score = min(max(50 + chg_pct * chg_w + intra_pct * intra_w, score_floor), score_ceil)
                if score < min_score:
                    continue
                direction = "LONG" if chg_pct > 0 else "SHORT"
                entry = round(price, 2)
                d     = 1 if direction == "LONG" else -1
                stop_loss   = round(entry * (1 - d * stop_pct), 2)
                take_profit = round(entry * (1 + d * tp_pct),   2)
                qty      = _kelly_qty(equity, entry, stop_loss, take_profit, score)
                rr       = round(tp_pct / stop_pct, 2)
                rationale = f"fallback chg={chg_pct:+.1f}% intra={intra_pct:+.1f}%"

            dollar_rsk  = round(abs(entry - stop_loss) * qty, 2)
            expires_iso = (datetime.utcnow() + timedelta(minutes=win_mins)).isoformat()

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
            })

        recs.sort(key=lambda x: x["composite_score"], reverse=True)
        _save(RECS_FILE, recs)
        logger.info("Scan complete: %d recs (agents=%s)", len(recs), _AGENTS_AVAILABLE)

        _scan_counter["n"] += 1
        if _scan_counter["n"] % 3 == 0:
            _update_strategy_weights()

    except Exception as exc:
        logger.exception("Market scan failed: %s", exc)


# === Background loop ===

async def _background_loop() -> None:
    await asyncio.sleep(5)
    await _run_market_scan()
    while True:
        await asyncio.sleep(300)
        try:
            await _run_market_scan()
        except Exception as exc:
            logger.error("Scanner error: %s", exc)
        try:
            async with aiohttp.ClientSession() as session:
                await _check_and_close_trades(session)
                await _revalidate_expired_recs(session)
        except Exception as exc:
            logger.error("Trade-check error: %s", exc)


# === FastAPI app ===

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_background_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Trading Bot API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExecuteBody(BaseModel):
    ticker:            str
    direction:         str
    qty:               int
    entry:             float
    stop_loss:         float
    take_profit:       float
    order_id:          Optional[str]   = None
    score:             Optional[float] = None
    recommendation_id: Optional[str]   = None


# === Routes ===

@app.get("/api/recommendations")
def get_recommendations():
    recs = _load(RECS_FILE, [])
    now  = datetime.utcnow()
    return [r for r in recs
            if not r.get("expires_at") or datetime.fromisoformat(r["expires_at"]) > now]


@app.get("/api/history")
def get_history():
    trades = _load(TRADES_FILE, [])
    closed = [t for t in trades if t.get("status") in ("closed", "cancelled")]
    return sorted(closed, key=lambda t: t.get("closed_at", ""), reverse=True)


@app.get("/api/pnl")
def get_pnl():
    trades  = _load(TRADES_FILE, [])
    closed  = [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None]
    by_date: Dict[str, float] = {}
    for t in closed:
        day = (t.get("closed_at") or t.get("executed_at") or "")[:10]
        if day:
            by_date[day] = round(by_date.get(day, 0.0) + float(t["pnl"]), 2)
    return [{"date": d, "pnl": p} for d, p in sorted(by_date.items())]


@app.get("/api/stats")
def get_stats():
    trades  = _load(TRADES_FILE, [])
    weights = _load_weights()
    closed  = [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None]
    wins    = [t for t in closed if float(t["pnl"]) > 0]
    losses  = [t for t in closed if float(t["pnl"]) <= 0]

    total_pnl = sum(float(t["pnl"]) for t in closed)
    win_rate  = len(wins) / len(closed) * 100 if closed else 0.0
    avg_win   = sum(float(t["pnl"]) for t in wins)   / len(wins)   if wins   else 0.0
    avg_loss  = sum(float(t["pnl"]) for t in losses) / len(losses) if losses else 0.0
    pf        = abs(avg_win * len(wins) / (avg_loss * len(losses))) if losses and avg_loss else 0.0

    return {
        "total_pnl":       round(total_pnl, 2),
        "win_rate":         round(win_rate, 1),
        "total_trades":     len(closed),
        "open_positions":   len([t for t in trades if t.get("status") == "open"]),
        "avg_win":          round(avg_win, 2),
        "avg_loss":         round(avg_loss, 2),
        "profit_factor":    round(pf, 2),
        "strategy_version": weights.get("update_count", 0),
        "win_rate_30d":     weights.get("win_rate_30d"),
        "bias":             weights.get("bias", "neutral"),
        "agents_active":    _AGENTS_AVAILABLE,
    }


@app.get("/api/regime")
def get_regime():
    recs   = _load(RECS_FILE, [])
    regime = recs[0].get("regime", "neutral") if recs else "neutral"
    return {"regime": regime, "updated_at": datetime.utcnow().isoformat()}


@app.get("/api/sectors")
def get_sectors():
    recs = _load(RECS_FILE, [])
    now  = datetime.utcnow()
    active = [r for r in recs if not r.get("expires_at") or datetime.fromisoformat(r["expires_at"]) > now]
    bucket: Dict[str, List[float]] = {}
    for r in active:
        bucket.setdefault(r.get("sector", "Other"), []).append(r["composite_score"])
    return sorted(
        [{"sector": s, "score": round(sum(v) / len(v), 1), "count": len(v)} for s, v in bucket.items()],
        key=lambda x: x["score"], reverse=True,
    )


@app.post("/api/execute")
def execute_trade(body: ExecuteBody):
    trades = _load(TRADES_FILE, [])
    record = {
        "id":              body.recommendation_id or str(uuid.uuid4()),
        "ticker":          body.ticker,
        "direction":       body.direction,
        "entry":           body.entry,
        "qty":             body.qty,
        "stop_loss":       body.stop_loss,
        "take_profit":     body.take_profit,
        "order_id":        body.order_id,
        "composite_score": body.score,
        "status":          "open",
        "executed_at":     datetime.utcnow().isoformat(),
        "pnl":             None,
        "pnl_pct":         None,
        "exit":            None,
        "exit_reason":     None,
        "closed_at":       None,
    }
    trades.append(record)
    _save(TRADES_FILE, trades)
    logger.info("Recorded trade: %s %s qty=%d entry=%.2f",
                body.direction, body.ticker, body.qty, body.entry)
    return {"ok": True, "trade_id": record["id"]}


@app.post("/api/scan")
async def trigger_scan():
    asyncio.create_task(_run_market_scan())
    return {"ok": True, "message": "Scan started"}


@app.get("/health")
def health():
    return {"status": "ok", "agents": _AGENTS_AVAILABLE, "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=False)
