"""Intraday walk-forward backtest — day-trade focused.

The lookback window is set by --days (default 30 for a standalone run; the
optimizer drives this same engine at 60). The filename is deliberately
window-agnostic — don't bake a day count into it.

Usage (from trading_bot/ directory):
    python backtest_intraday.py
    python backtest_intraday.py --days 30 --tickers NVDA TSLA AAPL MSFT
    python backtest_intraday.py --days 14 --top 15

What it does:
  1. Fetches --days of 5-min bars for each ticker via Alpaca REST.
  2. Every ~half-trading-day, runs the full agent pipeline (Technical + Fundamental + Risk).
  3. Simulates bracket orders: TP / SL / intraday-close (forces exit at 15:55 ET).
  4. Prints a summary table + writes backtest_results.json.

Day-trade rule: every position opened is closed by end of same calendar day.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import time as _time_mod
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional

from dotenv import load_dotenv

# Search for .env in: trading_bot/ -> project root -> dashboard/
_here = Path(__file__).parent
for _candidate in [
    _here / ".env",
    _here.parent / ".env",
    _here.parent / ".env.local",
    _here.parent / "trading-dashboard" / ".env.local",
]:
    if _candidate.exists():
        load_dotenv(_candidate, override=False)

import numpy as np
import pandas as pd

# -- path fix so we can import trading_bot packages ----------------------------
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import load_settings
from agents.regime_agent import classify_regime, _VIX_THRESHOLDS
from core.enums import Decision
from core.models import AnalysisContext, RiskParameters
from core.trade_stats import load_closed_trades, summarize, format_block
from core.paths import volume_dir
from bootstrap import active_broker, build_manager, build_news

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bt30")

RESULTS_FILE = (volume_dir() or Path(__file__).parent.parent) / "backtest_results.json"

# -- Slippage model ------------------------------------------------------------
SLIPPAGE_PCT = float(os.getenv("BACKTEST_SLIPPAGE_PCT", "0.0005"))  # 0.05% per side

# -- Strategy weights file -----------------------------------------------------
_WEIGHTS_FILE = Path(__file__).parent / "data" / "strategy_weights.json"


def _load_current_weights() -> dict:
    defaults = {"atr_stop_multiple": 2.0, "atr_target_multiple": 4.0}
    try:
        return {**defaults, **json.loads(_WEIGHTS_FILE.read_text())}
    except Exception:
        return defaults


# -- Data model ----------------------------------------------------------------

@dataclass
class TradeResult:
    ticker:      str
    direction:   str
    entry_time:  str
    exit_time:   str
    entry_price: float
    exit_price:  float
    qty:         float
    stop_loss:   float
    take_profit: float
    risk_reward: float
    outcome:     str   # TP_HIT | SL_HIT | EOD_CLOSE
    pnl_usd:     float
    pnl_pct:     float
    score:       float
    regime:      str = "unknown"   # risk_on | neutral | risk_off at entry (VIX-reconstructed)


# -- Alpaca historical bars ----------------------------------------------------

async def fetch_bars_range(
    symbol: str,
    start: datetime,
    end: datetime,
    key_id: str,
    secret: str,
    timeframe: str = "5Min",
) -> Optional[pd.DataFrame]:
    """Fetch date-range bars from Alpaca REST v2 with pagination."""
    import aiohttp

    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    headers = {
        "APCA-API-KEY-ID":     key_id,
        "APCA-API-SECRET-KEY": secret,
    }
    params = {
        "timeframe": timeframe,
        "start":     start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end":       end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "feed":      os.getenv("ALPACA_DATA_FEED", "iex"),
        "adjustment": "raw",
        "limit":     10000,
    }

    all_bars: list[dict] = []
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            try:
                async with session.get(url, params=params,
                                       timeout=aiohttp.ClientTimeout(total=30)) as r:
                    if r.status == 403:
                        logger.warning("%s: 403 -- subscription limit, skipping", symbol)
                        return None
                    r.raise_for_status()
                    data = await r.json()
            except Exception as exc:
                logger.warning("fetch_bars_range error for %s: %s", symbol, exc)
                return None

            bars = data.get("bars", [])
            all_bars.extend(bars)
            next_token = data.get("next_page_token")
            if not next_token or not bars:
                break
            params["page_token"] = next_token

    if not all_bars:
        logger.warning("%s: no bars returned", symbol)
        return None

    df = pd.DataFrame(all_bars)
    df = df.rename(columns={"t": "timestamp", "o": "open", "h": "high",
                             "l": "low", "c": "close", "v": "volume"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    logger.info("  %s: fetched %d bars (%s -> %s)",
                symbol, len(df),
                df.index[0].date(), df.index[-1].date())
    return df


def _store_bars(all_bars: dict, ticker: str, df: Optional[pd.DataFrame]) -> None:
    """Keep a ticker's bars only if there's enough history for the walk-forward."""
    if df is not None and not df.empty and len(df) >= LOOKBACK_BARS + 10:
        all_bars[ticker] = df
    else:
        n = 0 if df is None else len(df)
        logger.warning("Skipping %s -- not enough data (%d bars)", ticker, n)


async def _fetch_all_bars(
    settings,
    mode: str,
    fetch_list: list[str],
    start_dt: datetime,
    end_dt: datetime,
) -> dict[str, pd.DataFrame]:
    """Fetch historical bars for every ticker via the ACTIVE broker.

    IBKR mode: one TWS connection on a distinct clientId (so it won't clash with
    a running live bot), fetched sequentially to respect IBKR historical pacing.
    Alpaca mode: parallel REST, as before.
    """
    all_bars: dict[str, pd.DataFrame] = {}

    if mode == "ibkr":
        from execution.ibkr_broker import IBKRBroker
        bt_client_id = int(os.getenv("BACKTEST_IBKR_CLIENT_ID",
                                     str(settings.ibkr_client_id + 20)))
        logger.info("Fetching bars from IBKR/TWS %s:%s (clientId=%d), sequentially...",
                    settings.ibkr_host, settings.ibkr_port, bt_client_id)
        broker = IBKRBroker(settings.ibkr_host, settings.ibkr_port, bt_client_id)
        async with broker:
            for ticker in fetch_list:
                try:
                    df = await broker.get_bars_range(ticker, start_dt, end_dt, "5Min")
                except Exception as exc:
                    logger.warning("IBKR history fetch failed for %s: %s", ticker, exc)
                    df = None
                _store_bars(all_bars, ticker, df)
    else:
        logger.info("Fetching bars for %d tickers in parallel (Alpaca)...", len(fetch_list))
        results = await asyncio.gather(
            *[fetch_bars_range(t, start_dt, end_dt,
                               settings.alpaca_key_id, settings.alpaca_secret)
              for t in fetch_list],
            return_exceptions=True,
        )
        for ticker, result in zip(fetch_list, results):
            if isinstance(result, Exception):
                logger.warning("Failed to fetch bars for %s: %s", ticker, result)
                continue
            _store_bars(all_bars, ticker, result)

    return all_bars


# -- Fill simulator (day-trade: forced close at 15:55 ET) ----------------------

_ET = ZoneInfo("America/New_York")

def simulate_day_trade(
    future_bars: pd.DataFrame,
    *,
    direction: Decision,
    entry: float,
    stop_loss: float,
    take_profit: float,
    qty: float,
    slippage_pct: float = 0.0,
) -> tuple[str, float, str, float, float]:
    """Walk forward bars; force exit by 15:55 ET same calendar day."""
    entry_date = future_bars.index[0].astimezone(_ET).date() if len(future_bars) else date.today()
    mult = 1 if direction is Decision.LONG else -1

    for ts, bar in future_bars.iterrows():
        bar_date = ts.astimezone(_ET).date()
        bar_time = ts.astimezone(_ET).time()

        # Force EOD close at/after 15:55 on same day, or next day open
        if bar_date > entry_date or (bar_date == entry_date and bar_time.hour == 15 and bar_time.minute >= 55):
            exit_px = float(bar["open"])
            pnl = mult * (exit_px - entry) * qty
            pnl -= abs(entry) * slippage_pct * 2 * qty
            return "EOD_CLOSE", exit_px, str(ts), pnl, mult * (exit_px - entry) / entry * 100

        high = float(bar["high"])
        low  = float(bar["low"])

        if direction is Decision.LONG:
            sl_hit = low  <= stop_loss
            tp_hit = high >= take_profit
        else:
            sl_hit = high >= stop_loss
            tp_hit = low  <= take_profit

        if tp_hit and sl_hit:
            # Both triggered same bar -> worst case SL
            exit_px = stop_loss
            pnl = mult * (exit_px - entry) * qty
            pnl -= abs(entry) * slippage_pct * 2 * qty
            return "SL_HIT", exit_px, str(ts), pnl, mult * (exit_px - entry) / entry * 100
        elif tp_hit:
            exit_px = take_profit
            pnl = mult * (exit_px - entry) * qty
            pnl -= abs(entry) * slippage_pct * 2 * qty
            return "TP_HIT", exit_px, str(ts), pnl, mult * (exit_px - entry) / entry * 100
        elif sl_hit:
            exit_px = stop_loss
            pnl = mult * (exit_px - entry) * qty
            pnl -= abs(entry) * slippage_pct * 2 * qty
            return "SL_HIT", exit_px, str(ts), pnl, mult * (exit_px - entry) / entry * 100

    # Fallback: use last bar's close
    last_ts  = future_bars.index[-1]
    last_px  = float(future_bars["close"].iloc[-1])
    pnl = mult * (last_px - entry) * qty
    pnl -= abs(entry) * slippage_pct * 2 * qty
    return "EOD_CLOSE", last_px, str(last_ts), pnl, mult * (last_px - entry) / entry * 100


# -- SPY regime filter ---------------------------------------------------------

def _spy_regime_at(spy_bars: "pd.DataFrame | None", entry_ts: "pd.Timestamp") -> str:
    """Bull/bear/neutral from SPY's move on the same trading day up to entry_ts."""
    if spy_bars is None or spy_bars.empty:
        return "neutral"
    entry_date = entry_ts.astimezone(_ET).date()
    day_spy = spy_bars[spy_bars.index.map(lambda t: t.astimezone(_ET).date()) == entry_date]
    day_spy = day_spy[day_spy.index <= entry_ts]
    if len(day_spy) < 2:
        return "neutral"
    chg = (float(day_spy["close"].iloc[-1]) / float(day_spy["open"].iloc[0]) - 1) * 100
    if chg > 0.3:
        return "bull"
    elif chg < -0.3:
        return "bear"
    return "neutral"


# -- VIX-reconstructed regime (matches the live regime_agent rule) -------------

async def fetch_vix_daily(start: datetime, end: datetime) -> dict:
    """Daily CBOE VIX (^VIX) closes from Yahoo, keyed by date. {} on failure.

    Lets the backtest rebuild the SAME risk_on/neutral/risk_off labels the live
    regime agent uses, so per-regime tuning is measured on matching regimes."""
    import aiohttp
    rng_days = max(7, (end - start).days + 5)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX",
                params={"interval": "1d", "range": f"{rng_days}d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status != 200:
                    logger.warning("VIX history fetch returned %s", r.status)
                    return {}
                payload = await r.json()
        res    = payload["chart"]["result"][0]
        stamps = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
        out: dict = {}
        for ts, c in zip(stamps, closes):
            if c is not None:
                out[datetime.fromtimestamp(ts, tz=timezone.utc).date()] = round(float(c), 2)
        return out
    except Exception as exc:
        logger.warning("VIX history fetch failed: %s", exc)
        return {}


def _prior_vix(vix_by_date: dict, d) -> Optional[float]:
    """Most recent VIX close STRICTLY before date d (no same-day look-ahead)."""
    prior = [x for x in vix_by_date if x < d]
    return vix_by_date[max(prior)] if prior else None


def _session_vwap_chg(bars: "pd.DataFrame | None", entry_ts: "pd.Timestamp") -> tuple:
    """(price-vs-session-VWAP %, day-change %) using only bars up to entry_ts."""
    if bars is None or bars.empty:
        return None, None
    day = entry_ts.astimezone(_ET).date()
    sess = bars[bars.index <= entry_ts]
    sess = sess[sess.index.map(lambda t: t.astimezone(_ET).date()) == day]
    if sess.empty:
        return None, None
    typical = (sess["high"] + sess["low"] + sess["close"]) / 3
    cumvol  = float(sess["volume"].sum())
    vwap    = float((typical * sess["volume"]).sum() / cumvol) if cumvol > 0 else float(sess["close"].iloc[-1])
    last    = float(sess["close"].iloc[-1])
    open_   = float(sess["open"].iloc[0])
    return (last - vwap) / vwap * 100, (last - open_) / open_ * 100


def regime_at(entry_ts: "pd.Timestamp", spy_bars, qqq_bars, vix_by_date: dict) -> str:
    """Reconstruct the live risk_on/neutral/risk_off label at entry_ts, point-in-time."""
    spy_vw, spy_ch = _session_vwap_chg(spy_bars, entry_ts)
    qqq_vw, qqq_ch = _session_vwap_chg(qqq_bars, entry_ts)
    vix = _prior_vix(vix_by_date, entry_ts.astimezone(_ET).date()) if vix_by_date else None
    regime, _ = classify_regime(
        vix_level=vix, vix_thresholds=_VIX_THRESHOLDS,
        spy_vs_vwap=spy_vw, spy_day_chg=spy_ch,
        qqq_vs_vwap=qqq_vw, qqq_day_chg=qqq_ch,
    )
    return regime.value


# -- Walk-forward for one ticker -----------------------------------------------

LOOKBACK_BARS = 200   # bars fed to agents
STEP_BARS     = 6     # evaluate every ~30 min (6 x 5min = 30min) — matches live runner cadence
# Entry filter: skip evaluations where the entry bar falls after this UTC hour.
# 19:00 UTC = 15:00 ET -- no new entries in the last hour of RTH.
ENTRY_CUTOFF_UTC_HOUR = 19

# -- Research-derived entry filters --------------------------------------------
# Research #1 (Luo et al. 2023 / PEAD): skip the first 30 min of RTH.
OPEN_NOISE_UTC_HOUR   = 13   # 13:xx UTC = 9:xx ET
OPEN_NOISE_UTC_MINUTE = 30
OPEN_NOISE_END_MINUTE = 60

# Research #3 (Barber & Odean): minimum session volume confirmation ratio.
VOLUME_CONFIRM_RATIO = 1.3


async def backtest_ticker(
    pm,
    ticker: str,
    bars: pd.DataFrame,
    spy_bars: "pd.DataFrame | None" = None,
    qqq_bars: "pd.DataFrame | None" = None,
    vix_by_date: "dict | None" = None,
) -> list[TradeResult]:
    results: list[TradeResult] = []
    n = len(bars)
    sl_dates: set[date] = set()

    # ── Pre-compute vol_ratio tables once (O(n)), avoids O(n²) groupby in loop.
    _b_dates = [ts.astimezone(_ET).date() for ts in bars.index]
    _b_date_s = pd.Series(_b_dates, index=bars.index, dtype=object)
    _daily_vol: dict = bars.groupby(_b_date_s)["volume"].sum().to_dict()
    _sorted_days = sorted(_daily_vol.keys())
    _tmp = bars[["volume"]].copy()
    _tmp["_d"] = _b_dates
    _tmp["_cv"] = _tmp.groupby("_d")["volume"].cumsum()
    _tmp["_bc"] = _tmp.groupby("_d").cumcount()   # 0-based within day
    _cumvol: dict = _tmp["_cv"].to_dict()
    _barcnt: dict = _tmp["_bc"].to_dict()

    def _vol_ratio(entry_ts: pd.Timestamp, today_d) -> float:
        loc = bars.index.searchsorted(entry_ts, side="left") - 1
        if loc < 0 or _b_date_s.iloc[loc] != today_d:
            return 1.0
        prev_ts   = bars.index[loc]
        vol_sofar = float(_cumvol.get(prev_ts, 0.0))
        bar_cnt   = int(_barcnt.get(prev_ts, 0)) + 1
        frac      = min(bar_cnt / 78.0, 1.0)
        if frac < 0.05:
            return 1.0
        prior = [d for d in _sorted_days if d < today_d]
        if not prior:
            return 1.0
        tail = prior[-20:]
        avg_daily = sum(_daily_vol[d] for d in tail) / len(tail)
        return (vol_sofar / frac) / avg_daily if avg_daily > 0 else 1.0

    for i in range(LOOKBACK_BARS, n - 2, STEP_BARS):
        window = bars.iloc[i - LOOKBACK_BARS: i]
        entry_bar_idx = i
        entry_ts = bars.index[entry_bar_idx]

        if entry_ts.astimezone(timezone.utc).hour >= ENTRY_CUTOFF_UTC_HOUR:
            continue

        # Research #1 (PEAD): skip 9:30-10:00 ET open noise
        entry_et = entry_ts.astimezone(_ET)
        if entry_et.hour == 9:
            continue

        entry_date_et = entry_et.date()
        if entry_date_et in sl_dates:
            continue

        # Research #3: session volume confirmation (O(1) lookup)
        if _vol_ratio(entry_ts, entry_date_et) < VOLUME_CONFIRM_RATIO:
            continue

        ctx = AnalysisContext(
            ticker=ticker,
            bars=window,
            account={"equity": 100_000.0, "buying_power": 50_000.0},
            as_of=entry_ts,
            backtest_mode=True,
        )

        try:
            decision = await pm.decide(ctx)
        except Exception as exc:
            logger.warning("decide() failed for %s at %s: %s", ticker, entry_ts, exc)
            continue

        if not decision.is_actionable or decision.risk is None:
            continue

        # Regime filter: skip signals that fight the market
        regime = _spy_regime_at(spy_bars, entry_ts)
        if regime == "bear" and decision.decision is Decision.LONG:
            continue
        if regime == "bull" and decision.decision is Decision.SHORT:
            continue

        r = decision.risk
        # Entry at next bar's open
        if entry_bar_idx + 1 >= n:
            continue
        entry_price = float(bars["open"].iloc[entry_bar_idx + 1])

        # Recalculate SL/TP relative to actual fill price (keep ATR distances)
        sl_dist = abs(entry_price - r.stop_loss)
        tp_dist = abs(r.take_profit - entry_price)
        if decision.decision is Decision.LONG:
            sl = entry_price - sl_dist
            tp = entry_price + tp_dist
        else:
            sl = entry_price + sl_dist
            tp = entry_price - tp_dist

        future = bars.iloc[entry_bar_idx + 1:]
        outcome, exit_px, exit_ts, pnl, pnl_pct = simulate_day_trade(
            future,
            direction=decision.decision,
            entry=entry_price,
            stop_loss=sl,
            take_profit=tp,
            qty=float(r.qty),
            slippage_pct=SLIPPAGE_PCT,
        )

        results.append(TradeResult(
            ticker      = ticker,
            direction   = decision.decision.value,
            entry_time  = str(entry_ts),
            exit_time   = exit_ts,
            entry_price = round(entry_price, 4),
            exit_price  = round(exit_px, 4),
            qty         = float(r.qty),
            stop_loss   = round(sl, 4),
            take_profit = round(tp, 4),
            risk_reward = round(tp_dist / sl_dist, 3) if sl_dist > 0 else 0,
            outcome     = outcome,
            pnl_usd     = round(pnl, 2),
            pnl_pct     = round(pnl_pct, 4),
            score       = round(float(decision.composite_score), 1),
            regime      = regime_at(entry_ts, spy_bars, qqq_bars, vix_by_date or {}),
        ))

        if outcome == "SL_HIT":
            sl_dates.add(entry_date_et)

    return results


# -- Summary stats -------------------------------------------------------------

def calc_summary(all_trades: list[TradeResult]) -> dict:
    """Compute backtest stats dict. Used by both backtest and optimizer."""
    if not all_trades:
        return {}

    df = pd.DataFrame([asdict(t) for t in all_trades])
    total = len(df)
    wins   = int((df["outcome"] == "TP_HIT").sum())
    losses = int((df["outcome"] == "SL_HIT").sum())
    eods   = int((df["outcome"] == "EOD_CLOSE").sum())

    win_rate    = wins / total * 100 if total else 0.0
    total_pnl   = float(df["pnl_usd"].sum())
    gross_wins  = float(df.loc[df["pnl_usd"] > 0, "pnl_usd"].sum())
    gross_loss  = abs(float(df.loc[df["pnl_usd"] < 0, "pnl_usd"].sum()))
    # No losing trades → "infinite" PF, but Infinity is invalid JSON (the JS
    # dashboard's JSON.parse rejects it) and would also poison the optimizer's
    # ranking. Use a finite sentinel so results stay parseable and rankable.
    if gross_loss > 0:
        profit_factor = gross_wins / gross_loss
    else:
        profit_factor = 999.0 if gross_wins > 0 else 0.0

    avg_win  = float(df.loc[df["outcome"] == "TP_HIT",    "pnl_usd"].mean()) if wins  else 0.0
    avg_loss = float(df.loc[df["outcome"] == "SL_HIT",    "pnl_usd"].mean()) if losses else 0.0

    df_sorted = df.sort_values("entry_time")
    daily = df_sorted.groupby(df_sorted["entry_time"].str[:10])["pnl_usd"].sum()
    # Guard a zero/NaN daily-PnL std (one trading day, or identical daily P&L) —
    # otherwise Sharpe is inf/nan, which is invalid JSON for the dashboard.
    sharpe = 0.0
    if len(daily) > 1:
        sd = float(daily.std())
        if sd > 0 and not math.isnan(sd):
            sharpe = float(daily.mean() / sd * (252 ** 0.5))

    cum = df_sorted["pnl_usd"].cumsum()
    max_dd = float((cum - cum.cummax()).min())

    # True expected value per trade: mean realized P&L across ALL outcomes,
    # including EOD_CLOSE (forced 15:55 exits), which are usually the majority
    # of day trades. The old TP/SL-only formula silently ignored them, so the
    # optimizer was maximizing a number that excluded most actual trade results.
    ev_per_trade = total_pnl / total if total else 0.0

    by_tk = df.groupby("ticker").agg(
        trades=("pnl_usd", "count"),
        pnl=("pnl_usd", "sum"),
        win_rate=("outcome", lambda x: (x == "TP_HIT").mean() * 100),
    ).sort_values("pnl", ascending=False)

    by_regime = []
    if "regime" in df.columns:
        rg = df.groupby("regime").agg(
            trades=("pnl_usd", "count"),
            pnl=("pnl_usd", "sum"),
            win_rate=("outcome", lambda x: (x == "TP_HIT").mean() * 100),
            ev_per_trade=("pnl_usd", "mean"),
        ).round(2)
        by_regime = rg.reset_index().to_dict(orient="records")

    return {
        "total_trades":   total,
        "wins":           wins,
        "losses":         losses,
        "eods":           eods,
        "win_rate":       round(win_rate, 2),
        "total_pnl":      round(total_pnl, 2),
        "avg_win":        round(avg_win, 2),
        "avg_loss":       round(avg_loss, 2),
        "profit_factor":  round(profit_factor, 3),
        "sharpe":         round(sharpe, 3),
        "max_drawdown":   round(max_dd, 2),
        "ev_per_trade":   round(ev_per_trade, 2),
        "by_ticker":      by_tk.reset_index().to_dict(orient="records"),
        "by_regime":      by_regime,
        "trades":         [asdict(t) for t in all_trades],
    }


def print_summary(all_trades: list[TradeResult]) -> dict:
    """Compute + pretty-print backtest stats. Returns the stats dict."""
    summary = calc_summary(all_trades)
    if not summary:
        print("\n  No trades were generated. Check API keys and data availability.\n")
        return {}

    total  = summary["total_trades"]
    wins   = summary["wins"]
    losses = summary["losses"]
    eods   = summary["eods"]

    sep = "-" * 52
    print("\n" + "=" * 52)
    print("  Backtest Summary -- Day Trades Only")
    print("=" * 52)
    print(f"  Total trades  : {total}")
    print(f"  TP Hit        : {wins}  ({summary['win_rate']:.1f}%)")
    print(f"  SL Hit        : {losses}")
    print(f"  EOD Close     : {eods}")
    print(sep)
    print(f"  Total P&L     : ${summary['total_pnl']:+.2f}")
    print(f"  EV / trade    : ${summary['ev_per_trade']:+.2f}")
    print(f"  Avg Win       : ${summary['avg_win']:+.2f}")
    print(f"  Avg Loss      : ${summary['avg_loss']:+.2f}")
    print(f"  Profit Factor : {summary['profit_factor']:.2f}")
    print(f"  Sharpe Ratio  : {summary['sharpe']:.2f}")
    print(f"  Max Drawdown  : ${summary['max_drawdown']:.2f}")
    print(sep)

    df = pd.DataFrame([asdict(t) for t in all_trades])
    by_tk = df.groupby("ticker").agg(
        trades=("pnl_usd", "count"),
        pnl=("pnl_usd", "sum"),
        win_rate=("outcome", lambda x: (x == "TP_HIT").mean() * 100),
    ).sort_values("pnl", ascending=False)

    print(f"  {'Ticker':<8} {'Trades':>6} {'Win%':>6} {'P&L':>10}")
    print(f"  {sep}")
    for tk, row in by_tk.iterrows():
        marker = "+" if row["pnl"] > 0 else "-"
        print(f"  {marker} {tk:<6} {int(row['trades']):>6} {row['win_rate']:>5.1f}% {row['pnl']:>+10.2f}")
    print("=" * 52 + "\n")

    return summary


# -- Weight learning -----------------------------------------------------------

def _update_weights_from_backtest(backtest_trades: list) -> None:
    """Combine backtest results with live closed trades and update strategy weights."""
    live_trades_file = Path(__file__).parent / "data" / "trades.json"
    live_closed: list = []
    if live_trades_file.exists():
        try:
            live_closed = [
                t for t in json.loads(live_trades_file.read_text())
                if t.get("status") == "closed" and t.get("pnl") is not None
            ]
        except Exception:
            pass

    # Convert backtest trades to same dict shape as live trades
    bt_dicts = [
        {"pnl": t.pnl_usd, "direction": t.direction.upper(), "status": "closed"}
        for t in backtest_trades
    ]

    # Combined dataset: live trades counted 3x (recency bonus) + all backtest
    combined = bt_dicts + live_closed[-20:] + live_closed[-20:] + live_closed[-20:]
    if len(combined) < 10:
        logger.info("Not enough data for weight update (%d trades)", len(combined))
        return

    pnls   = [t.get("pnl", 0) or 0 for t in combined]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(combined)

    cur = _load_current_weights()
    cur["win_rate_30d"]       = round(win_rate * 100, 1)
    cur["bt_trades"]          = len(bt_dicts)
    cur["live_trades_used"]   = len(live_closed)
    cur["last_bt_update"]     = datetime.utcnow().isoformat()
    cur["update_count"]       = cur.get("update_count", 0) + 1

    avg_win  = float(np.mean(wins))   if wins   else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    profit_factor = (avg_win * len(wins)) / abs(avg_loss * len(losses)) \
                    if losses and avg_loss != 0 else 2.0

    if win_rate > 0.58 and profit_factor > 1.4:
        cur["atr_target_multiple"] = round(min(5.5, cur.get("atr_target_multiple", 4.0) * 1.05), 3)
    elif win_rate < 0.40 or profit_factor < 0.9:
        cur["atr_stop_multiple"]   = round(max(1.0, cur.get("atr_stop_multiple",   2.0) * 0.95), 3)
        cur["atr_target_multiple"] = round(max(2.0, cur.get("atr_target_multiple", 4.0) * 0.97), 3)

    long_trades  = [t for t in combined if t.get("direction", "").upper() == "LONG"]
    short_trades = [t for t in combined if t.get("direction", "").upper() == "SHORT"]
    lwr = len([t for t in long_trades  if (t.get("pnl") or 0) > 0]) / len(long_trades)  if long_trades  else 0.5
    swr = len([t for t in short_trades if (t.get("pnl") or 0) > 0]) / len(short_trades) if short_trades else 0.5
    if lwr > swr + 0.15:
        cur["bias"] = "long"
    elif swr > lwr + 0.15:
        cur["bias"] = "short"
    else:
        cur["bias"] = "neutral"

    _WEIGHTS_FILE.parent.mkdir(exist_ok=True)
    _WEIGHTS_FILE.write_text(json.dumps(cur, indent=2))
    logger.info(
        "Weights updated from combined analysis — win_rate=%.1f%% PF=%.2f "
        "bias=%s atr_stop=%.2f atr_tp=%.2f (bt=%d live=%d)",
        win_rate * 100, profit_factor, cur["bias"],
        cur["atr_stop_multiple"], cur["atr_target_multiple"],
        len(bt_dicts), len(live_closed),
    )


# -- Dynamic lookback window ---------------------------------------------------
# A fixed 30/60-day window is arbitrary. These bound a window that is sized per
# run to the data + signal density, never below the floor or above the cap (also
# capped by however much intraday history the data feed actually returns).
WINDOW_FLOOR = int(os.getenv("BACKTEST_WINDOW_FLOOR", "30"))
WINDOW_CAP   = int(os.getenv("BACKTEST_WINDOW_CAP",  "120"))
# Minimum trades for a single backtest to be statistically meaningful.
MIN_TRADES_FOR_SIGNIFICANCE = int(os.getenv("BACKTEST_MIN_TRADES", "20"))


def choose_window_days(trades_in_full: int, full_days: int, *, floor: int, cap: int,
                       min_is: int, min_oos: int, split_frac: float,
                       margin: float = 1.3) -> int:
    """Smallest lookback in [floor, cap] projected to yield enough trades.

    Smart, case-by-case: from the trade *density* observed over the full fetched
    window, pick the shortest window that still clears the statistical minimums —
    dense signals → short, recent window; sparse signals → longer window. For a
    walk-forward split BOTH the in-sample (split_frac) and OOS (1-split_frac)
    slices must clear their minimum; for a single run pass split_frac=1.0,
    min_oos=0. `margin` is a safety cushion since trades aren't perfectly linear.
    """
    floor = max(1, min(floor, cap))
    if full_days <= 0 or trades_in_full <= 0:
        return cap  # no signal/data read — fall back to all the history we have
    density = trades_in_full / full_days
    need_is = (min_is * margin) / (density * split_frac) if split_frac > 0 else 0.0
    need_oos = ((min_oos * margin) / (density * (1.0 - split_frac))
                if (min_oos > 0 and split_frac < 1.0) else 0.0)
    needed = max(need_is, need_oos)
    return max(floor, min(cap, int(math.ceil(needed))))


def data_span_days(bars_map: dict) -> int:
    """Calendar-day span of the longest series in the fetched bars (0 if none)."""
    span = 0
    for df in bars_map.values():
        if df is not None and len(df) > 1:
            span = max(span, int((df.index[-1] - df.index[0]).days))
    return span


def trim_bars(bars_map: dict, days: int, end_dt: datetime) -> dict:
    """Keep only the last `days` of each series, dropping any left too short to
    run a walk-forward (fewer than one full lookback + a margin of bars)."""
    cutoff = pd.Timestamp(end_dt - timedelta(days=days))
    out: dict = {}
    for tk, df in bars_map.items():
        trimmed = df[df.index >= cutoff]
        if len(trimmed) >= LOOKBACK_BARS + 10:
            out[tk] = trimmed
    return out


# -- Main ----------------------------------------------------------------------

# Fallback only — used when the universe scanner fails and no --tickers given.
_FALLBACK_TICKERS = [
    "NVDA", "TSLA", "AAPL", "MSFT", "AMD",
    "META", "AMZN", "GOOGL", "SPY", "QQQ",
]


async def run(tickers: list[str], days="auto") -> None:
    settings = load_settings()
    dynamic = isinstance(days, str) and str(days).lower() == "auto"

    # When no explicit ticker list is provided, pull today's top movers from
    # Alpaca's universe scanner instead of the hardcoded fallback list.
    if not tickers:
        try:
            from data.universe_scanner import UniverseScanner
            scanner = UniverseScanner(settings.alpaca_key_id, settings.alpaca_secret)
            n = int(os.getenv("BACKTEST_TOP_N", "30"))
            logger.info("Universe scanner: fetching top %d candidates…", n)
            tickers = await scanner.get_candidates(top_n=n)
            if tickers:
                logger.info("Universe: %s", " ".join(tickers))
            else:
                logger.warning("Universe scanner returned 0 candidates (holiday/off-hours?) — using fallback list")
                tickers = _FALLBACK_TICKERS
        except Exception as exc:
            logger.warning("Universe scanner failed (%s) — using fallback list", exc)
            tickers = _FALLBACK_TICKERS

    mode = active_broker(settings)
    logger.info("Backtest data source: %s (follows the dashboard broker toggle)", mode.upper())

    if mode == "alpaca" and (not settings.alpaca_key_id or not settings.alpaca_secret):
        logger.error("Alpaca data mode needs ALPACA_API_KEY_ID + ALPACA_API_SECRET in .env "
                     "(or flip the broker toggle to IBKR with TWS running).")
        return

    # Log real trade history so the run learns from the history tab.
    _hist_stats = summarize(load_closed_trades())
    logger.info(format_block(_hist_stats))

    fetch_days = WINDOW_CAP if dynamic else int(days)
    end_dt   = datetime.now(tz=timezone.utc).replace(hour=23, minute=59, second=59)
    start_dt = end_dt - timedelta(days=fetch_days + 5)   # buffer for weekends

    logger.info("Backtest window: %s -> %s  (fetch %d days%s)",
                start_dt.date(), end_dt.date(), fetch_days,
                ", auto-sizing" if dynamic else "")

    fetch_list = list(dict.fromkeys(tickers + ["SPY", "QQQ"]))
    vix_by_date = await fetch_vix_daily(start_dt, end_dt)
    logger.info("Tickers: %s (+ SPY for RS signal)", " ".join(tickers))

    all_bars = await _fetch_all_bars(settings, mode, fetch_list, start_dt, end_dt)

    # Apply self-tuned parameters (overrides any hardcoded defaults)
    cur_w = _load_current_weights()
    os.environ["ATR_STOP_MULTIPLE"]   = str(cur_w.get("atr_stop_multiple", 2.0))
    os.environ["ATR_TARGET_MULTIPLE"] = str(cur_w.get("atr_target_multiple", 4.0))

    settings = load_settings()

    # Full honest agent set: Technical, Fundamental, Risk, Liquid (all bars/math
    # based, no look-ahead) plus Insider/Squeeze/Macro wired in but self-neutralised
    # via ctx.backtest_mode (their data is point-in-time current → look-ahead if
    # replayed on history). Vision/Decision are LLM-gated and skipped here for the
    # daily auto-run (set USE_LLM_BACKTEST=true to include them).
    use_llm = os.getenv("USE_LLM_BACKTEST", "false").lower() in ("1", "true", "yes")
    pm = build_manager(
        settings, broker=None,
        include_live_only_agents=True,
        include_vision=use_llm,
        include_decision_agent=use_llm,
    )
    if "SPY" in all_bars:
        pm.technical.spy_bars = all_bars["SPY"]
        logger.info("SPY bars injected into TechnicalAgent (%d bars)", len(all_bars["SPY"]))

    all_trades: list[TradeResult] = []
    _bt_start = _time_mod.monotonic()
    _bt_total = sum(1 for t in tickers if t in all_bars)
    _bt_done  = 0

    for ticker in tickers:
        if ticker not in all_bars:
            continue
        bars = all_bars[ticker]
        logger.info("Running walk-forward for %s (%d bars)...", ticker, len(bars))
        trades = await backtest_ticker(pm, ticker, bars, spy_bars=all_bars.get("SPY"),
                                       qqq_bars=all_bars.get("QQQ"), vix_by_date=vix_by_date)
        all_trades.extend(trades)
        _bt_done += 1
        elapsed = _time_mod.monotonic() - _bt_start
        eta     = int(elapsed / _bt_done * (_bt_total - _bt_done)) if _bt_done else 0
        logger.info("PROGRESS: %d/%d (%.0f%%) ETA: %ds | %s done: %d trades P&L=$%.2f",
                    _bt_done, _bt_total, 100 * _bt_done / _bt_total, eta,
                    ticker, len(trades), sum(t.pnl_usd for t in trades))

    # Smart window: size to the last N days that hold enough trades to matter,
    # from the density observed over the full fetched history. Reported metrics
    # then reflect that recent, statistically-meaningful window.
    if dynamic:
        span   = data_span_days(all_bars)
        chosen = choose_window_days(
            len(all_trades), span,
            floor=WINDOW_FLOOR, cap=min(WINDOW_CAP, span or WINDOW_CAP),
            min_is=MIN_TRADES_FOR_SIGNIFICANCE, min_oos=0, split_frac=1.0,
        )
        cutoff = pd.Timestamp(end_dt - timedelta(days=chosen))
        kept = [t for t in all_trades if pd.Timestamp(t.entry_time) >= cutoff]
        logger.info("Dynamic window: %d trades over ~%dd → reporting last %dd (%d trades)",
                    len(all_trades), span, chosen, len(kept))
        all_trades = kept

    summary = print_summary(all_trades)

    # Always write results so the dashboard shows something (even "0 trades").
    out = summary if summary else {
        "total_trades": 0, "wins": 0, "losses": 0, "eods": 0,
        "win_rate": 0.0, "total_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        "profit_factor": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
        "ev_per_trade": 0.0, "by_ticker": [], "trades": [],
        "message": "No trades generated — signals may be insufficient or tickers had no setups",
    }
    RESULTS_FILE.write_text(json.dumps(out, indent=2, default=str))
    logger.info("Results saved to %s", RESULTS_FILE)

    _update_weights_from_backtest(all_trades)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Intraday day-trade backtest (window set by --days)")
    parser.add_argument("--days",    default="auto",
                        help="Lookback days, or 'auto' to size dynamically (default: auto)")
    parser.add_argument("--tickers", nargs="+", default=[],
                        help="Explicit ticker list (default: auto from universe scanner)")
    parser.add_argument("--top",     type=int, default=0,
                        help="Override top-N for universe scanner (default: BACKTEST_TOP_N env or 30)")
    args = parser.parse_args()

    async def _run():
        tickers = [t.upper() for t in args.tickers]

        # --top overrides BACKTEST_TOP_N env for one-off CLI runs
        if args.top > 0:
            os.environ["BACKTEST_TOP_N"] = str(args.top)

        # Empty tickers → run() will call the universe scanner itself
        await run(tickers, args.days)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
