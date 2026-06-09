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
_tech_agent        = None
_risk_agent        = None
_fundamental_agent = None
_vision_agent      = None
_social_agent      = None
_liquid_agent      = None
_ai4trade_client   = None
_Decision          = None

# Agent composite weights (must sum to 1.0 across active agents)
_AGENT_WEIGHTS = {
    "technical":   0.35,
    "fundamental": 0.20,
    "vision":      0.15,
    "social":      0.15,
    "liquid":      0.15,
}

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

    # ── FundamentalAgent (news sentiment via Alpaca news + optional Claude LLM) ──
    try:
        from agents.fundamental_agent import FundamentalAgent
        from data.news_sources import AlpacaNewsSource

        class _NewsAdapter:
            """Wraps AlpacaNewsSource.fetch_headlines() → .get_news() interface."""
            def __init__(self, src):
                self._src = src
            async def get_news(self, ticker: str, limit: int = 8):
                try:
                    headlines = await self._src.fetch_headlines(ticker, limit=limit)
                    return [{"headline": h.title, "summary": h.summary} for h in headlines]
                except Exception:
                    return []

        _alpaca_key_tmp    = os.getenv("ALPACA_API_KEY_ID", "")
        _alpaca_secret_tmp = os.getenv("ALPACA_API_SECRET", "")
        _anthropic_key     = os.getenv("ANTHROPIC_API_KEY", "")
        _gemini_key        = os.getenv("GEMINI_API_KEY", "")
        _llm_provider      = "gemini" if _gemini_key else ("anthropic" if _anthropic_key else "keyword")
        _news_adapter      = _NewsAdapter(AlpacaNewsSource(_alpaca_key_tmp, _alpaca_secret_tmp))
        _fundamental_agent = FundamentalAgent(
            _news_adapter,
            weight=0.20,
            anthropic_api_key=_anthropic_key,
            gemini_api_key=_gemini_key,
            max_articles=6,
        )
        logger.info("FundamentalAgent loaded (provider=%s)", _llm_provider)
    except Exception as _e:
        logger.warning("FundamentalAgent unavailable: %s", _e)

    # ── VisionAgent (chart image → vision LLM) ─────────────────────────────────
    try:
        from agents.vision_agent import VisionAgent
        _anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        _gemini_key    = os.getenv("GEMINI_API_KEY", "")
        _vision_agent  = VisionAgent(
            weight=0.15,
            anthropic_api_key=_anthropic_key,
            gemini_api_key=_gemini_key,
        )
        _vis_provider = "gemini" if _gemini_key else ("anthropic" if _anthropic_key else "none")
        logger.info("VisionAgent loaded (provider=%s)", _vis_provider)
    except Exception as _e:
        logger.warning("VisionAgent unavailable: %s", _e)

    # ── SocialAgent (AI4Trade community feed — public, no auth required) ───────
    try:
        from agents.social_agent import SocialSentimentAgent
        from data.ai4trade_client import AI4TradeClient as _AI4TC
        _ai4trade_client = _AI4TC(
            email=os.getenv("AI4TRADE_EMAIL", ""),
            password=os.getenv("AI4TRADE_PASSWORD", ""),
        )
        _social_agent = SocialSentimentAgent(_ai4trade_client, weight=0.15)
        logger.info("SocialAgent loaded (AI4Trade client ready)")
    except Exception as _e:
        logger.warning("SocialAgent unavailable: %s", _e)

    # ── LiquidAgent (crowd positioning / funding rate) ─────────────────────────
    try:
        from agents.liquid_agent import LiquidAgent
        _liquid_agent = LiquidAgent(weight=0.15, api_key=os.getenv("LIQUID_API_KEY", ""))
        logger.info("LiquidAgent loaded")
    except Exception as _e:
        logger.warning("LiquidAgent unavailable: %s", _e)

except Exception as _import_err:
    logger.warning("Agent imports failed (%s) -- scanner using fallback formula", _import_err)


async def _run_all_agents(ctx: "AnalysisContext") -> tuple:
    """Run all available agents in parallel and return (composite_score, evaluations).

    Only agents that are instantiated and return non-neutral confidence contribute
    to the weighted composite. Falls back to TechnicalAgent alone if others fail.
    """
    if not _AGENTS_AVAILABLE:
        return 50.0, []

    # Build chart image path for VisionAgent (render async in thread)
    chart_path = None
    if _vision_agent is not None and ctx.bars is not None:
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

    # Ensure AI4Trade client session is open
    if _ai4trade_client is not None and _ai4trade_client._session is None:
        try:
            import aiohttp as _aio
            _ai4trade_client._session = _aio.ClientSession(
                timeout=_aio.ClientTimeout(total=15.0)
            )
        except Exception:
            pass

    # Collect agents to run
    agents_to_run = []
    if _tech_agent is not None:
        agents_to_run.append(("technical", _tech_agent))
    if _fundamental_agent is not None:
        agents_to_run.append(("fundamental", _fundamental_agent))
    if _vision_agent is not None:
        agents_to_run.append(("vision", _vision_agent))
    if _social_agent is not None:
        agents_to_run.append(("social", _social_agent))
    if _liquid_agent is not None:
        agents_to_run.append(("liquid", _liquid_agent))

    # Run all in parallel with a per-agent timeout
    async def _safe_eval(name, agent):
        try:
            return name, await asyncio.wait_for(agent.safe_evaluate(ctx), timeout=8.0)
        except Exception as e:
            logger.debug("Agent %s failed for %s: %s", name, ctx.ticker, e)
            return name, None

    results = await asyncio.gather(*[_safe_eval(n, a) for n, a in agents_to_run])

    # Weighted composite — only count agents with confidence > 0.15
    evaluations = []
    total_w = 0.0
    weighted_sum = 0.0
    for name, ev in results:
        if ev is None:
            continue
        evaluations.append(ev)
        if ev.confidence > 0.15:
            w = _AGENT_WEIGHTS.get(name, 0.10)
            weighted_sum += ev.score * w
            total_w += w

    composite = weighted_sum / total_w if total_w > 0 else 50.0

    # Clean up temp chart file
    if chart_path:
        try:
            import os as _os
            _os.unlink(chart_path)
        except Exception:
            pass

    return composite, evaluations


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

        if _AGENTS_AVAILABLE and _tech_agent is not None:
            _tech_agent.spy_bars = bars_map.get("SPY")

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
                vix_approx = round(float(vixy_df.iloc[-1]["close"]) * 10, 1)
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
                ctx   = AnalysisContext(ticker=sym, bars=df, account={"equity": equity})
                score, agent_evals = await _run_all_agents(ctx)
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
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())) as session:
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    history = _load(HISTORY_FILE, [])
    if not isinstance(history, list):
        history = []

    total_trades = len(history)
    wins = [t for t in history if float(t.get("pnl") or t.get("realized_pnl") or 0) > 0]
    win_rate = (len(wins) / total_trades * 100) if total_trades else 0.0
    total_pnl = sum(float(t.get("pnl") or t.get("realized_pnl") or 0) for t in history)

    # Sharpe, max drawdown, avg R/R
    pnls = [float(t.get("pnl") or t.get("realized_pnl") or 0) for t in history]
    import numpy as _np
    sharpe = 0.0
    if len(pnls) >= 2:
        arr = _np.array(pnls)
        std = float(_np.std(arr))
        if std > 0:
            sharpe = round(float(_np.mean(arr)) / std * (_np.sqrt(252)), 2)

    max_dd = 0.0
    if pnls:
        cum = _np.cumsum(_np.array(pnls))
        peak = _np.maximum.accumulate(cum)
        dd = cum - peak
        max_dd = round(float(dd.min()), 2)

    rr_vals = [float(t.get("risk_reward") or 0) for t in history if t.get("risk_reward")]
    avg_rr  = round(sum(rr_vals) / len(rr_vals), 2) if rr_vals else 0.0

    weights = _load_weights()
    return {
        "total_pnl":      round(total_pnl, 2),
        "today_pnl":      0.0,
        "win_rate":       round(win_rate, 1),
        "total_trades":   total_trades,
        "open_positions": 0,
        "sharpe_ratio":   sharpe,
        "max_drawdown":   max_dd,
        "avg_rr":         avg_rr,
        "strategy_version": weights.get("update_count", 0),
        "win_rate_30d":   weights.get("win_rate_30d"),
        "bias":           weights.get("bias", "neutral"),
        "agents_active":  _AGENTS_AVAILABLE,
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
        return []
    open_trades = [t for t in trades if t.get("status") == "open"]
    return [
        {
            "ticker":          t.get("ticker", ""),
            "direction":       t.get("direction", "LONG"),
            "entry":           float(t.get("entry", 0)),
            "stop_loss":       float(t.get("stop_loss") or 0) or None,
            "take_profit":     float(t.get("take_profit") or 0) or None,
            "qty":             int(t.get("qty", 1)),
            "composite_score": t.get("composite_score"),
            "order_id":        t.get("order_id") or None,
            "executed_at":     t.get("executed_at") or None,
        }
        for t in open_trades
    ]


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


@app.get("/api/health")
def health():
    return {"status": "ok", "agents": _AGENTS_AVAILABLE, "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=False)
