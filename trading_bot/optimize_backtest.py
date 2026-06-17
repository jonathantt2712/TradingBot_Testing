"""Fast backtest optimizer — agent scores cached, grid-search runs in memory.

Architecture
============
Phase 1  Test 5 time windows (60/90/120/180/252 days) with base params.
         Bars are fetched ONCE for 252 days; each window is a date slice.
         For each window: run the full agent pipeline ONCE (score collection),
         then immediately test base params. Picks window with best EV/trade
         that also has >= MIN_TRADES trades.

Phase 2  Grid-search 96 parameter combos on the winning window.
         Agent scores are already cached -- replay is pure Python (no API calls).
         ~100x faster than the original approach.

Phase 3  Final run with optimal settings; save backtest_optimal.json +
         OPTIMAL_CONFIG.txt; update .env automatically.

Usage
=====
    python optimize_backtest.py
    python optimize_backtest.py --tickers NVDA TSLA AAPL MSFT AMD META AMZN GOOGL
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

# -- .env loading --------------------------------------------------------------
from dotenv import load_dotenv
_here = Path(__file__).parent
for _c in [
    _here / ".env",
    _here.parent / ".env",
    _here.parent / ".env.local",
    _here.parent / "trading-dashboard" / ".env.local",
]:
    if _c.exists():
        load_dotenv(_c, override=False)

sys.path.insert(0, str(_here))

from backtest_30day import (
    fetch_bars_range, simulate_day_trade, calc_summary, print_summary,
    TradeResult, LOOKBACK_BARS, STEP_BARS, ENTRY_CUTOFF_UTC_HOUR, _ET,
    DEFAULT_TICKERS, SLIPPAGE_PCT,
)
from config.settings import load_settings
from core.enums import Decision
from core.models import AnalysisContext
from agents.fundamental_agent import FundamentalAgent
from agents.risk_agent import RiskAgent
from agents.technical_agent import TechnicalAgent
from execution.portfolio_manager import PortfolioManager

# -- Logging -------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("opt")

# -- Search spaces -------------------------------------------------------------
# Use 90/120/180/252 — 60 days always yields <MIN_TRADES with our thresholds
TIME_WINDOWS = [90, 120, 180, 252]   # calendar days

# Coarser step for optimization passes (3× faster than STEP_BARS=26).
# Only the final Phase-3 run uses the full STEP_BARS for accuracy.
OPT_STEP = 78   # ~6.5 hours between evaluations during grid search

PARAM_GRID: dict[str, list[Any]] = {
    "LONG_THRESHOLD":      [63, 65, 67, 70],
    "SHORT_THRESHOLD":     [30, 33, 35, 37],
    "ATR_STOP_MULTIPLE":   [1.5, 2.0],
    "ATR_TARGET_MULTIPLE": [3.0, 4.0, 5.0],
}

MIN_TRADES   = 15          # discard configs with too few trades
EQUITY       = 100_000.0
RISK_PCT     = 0.01        # 1% risk per trade
MAX_POS_PCT  = 0.20        # 20% max position
MIN_RR       = 1.5         # minimum acceptable R:R

RESULTS_FILE = _here.parent / "backtest_optimal.json"
CONFIG_FILE  = _here.parent / "OPTIMAL_CONFIG.txt"
ENV_FILE     = _here.parent / ".env"


# -- Progress reporter (works in log files -- no \r) ---------------------------

class Progress:
    """Simple progress reporter that works when stdout is redirected to a file."""

    def __init__(self, total: int, desc: str = "") -> None:
        self.total = total
        self.desc  = desc
        self.start = time.time()
        self._last = 0

    def update(self, n: int, extra: str = "") -> None:
        interval = max(1, min(10, self.total // 20))
        if n - self._last < interval and n < self.total:
            return
        self._last = n
        elapsed = time.time() - self.start
        pct     = n / self.total if self.total else 1.0
        eta     = elapsed / pct * (1 - pct) if pct > 0 else 0
        filled  = int(pct * 32)
        bar     = "#" * filled + "." * (32 - filled)
        suffix  = f"  {extra}" if extra else ""
        logger.info("  %s [%s] %d/%d (%.0f%%)  ETA %ds%s",
                    self.desc, bar, n, self.total, pct * 100, eta, suffix)

    def done(self) -> None:
        elapsed = time.time() - self.start
        bar = "#" * 32
        logger.info("  %s [%s] %d/%d (100%%)  %.1fs total",
                    self.desc, bar, self.total, self.total, elapsed)


# -- Cached evaluation record --------------------------------------------------

@dataclass
class EvalRecord:
    ticker:        str
    entry_date_et: date          # calendar date ET (for same-day cooldown)
    entry_ts:      pd.Timestamp  # full timestamp (logging)
    entry_bar_idx: int           # position in full bars df
    composite:     float         # blended agent score [1..100]
    atr:           float         # ATR at evaluation time
    entry_price:   float         # next bar's open (actual fill price)
    lottery:       bool  = False  # Research #2: lottery stock profile detected
    vol_ratio:     float = 1.0   # Research #3: session vol vs 20-day avg (for gate in replay)


# -- ATR helper (matches RiskAgent._atr exactly) -------------------------------

def _is_lottery(window: pd.DataFrame) -> bool:
    """Research #2 (CPT): detect retail-frenzy / lottery stock profile.

    Returns True if the stock moved >12% in the last 20 bars AND has
    projected daily volume > 2.5× the 20-day average. These setups
    require tighter stop-losses (0.6× normal) to survive mean-reversions.
    """
    if len(window) < 25:
        return False
    recent = window.iloc[-20:]
    price_move = abs(
        (float(recent["close"].iloc[-1]) - float(recent["open"].iloc[0]))
        / max(float(recent["open"].iloc[0]), 1e-6)
    )
    if price_move < 0.12:
        return False
    # Estimate volume surge using last 20 bars vs prior 20-day avg
    today = window.index[-1].date()
    today_bars = window[window.index.map(lambda x: x.date()) == today]
    prior = window[window.index.map(lambda x: x.date()) < today]
    if prior.empty or today_bars.empty:
        return False
    daily_vols = prior.groupby(prior.index.date)["volume"].sum()
    avg_daily = float(daily_vols.tail(20).mean())
    if avg_daily <= 0:
        return False
    bars_per_day = 78
    fraction = min(len(today_bars) / bars_per_day, 1.0)
    projected = float(today_bars["volume"].sum()) / max(fraction, 0.05)
    return (projected / avg_daily) >= 2.5


def _atr14(window: pd.DataFrame) -> float:
    high, low, close = window["high"], window["low"], window["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    val = tr.rolling(14).mean().iloc[-1]
    return float(val) if not np.isnan(val) else 0.0


# -- PortfolioManager factory --------------------------------------------------

def _build_pm(spy_bars: Optional[pd.DataFrame] = None) -> PortfolioManager:
    settings = load_settings()
    tech = TechnicalAgent(weight=settings.weights.technical)
    if spy_bars is not None:
        tech.spy_bars = spy_bars
    return PortfolioManager(
        settings=settings,
        broker=None,
        fundamental=FundamentalAgent(
            news_source=None,
            weight=settings.weights.fundamental,
            anthropic_api_key="",
            model="",
        ),
        vision=None,
        technical=tech,
        risk=RiskAgent(settings.risk),
    )


# -- Score collection pass (async, runs once per ticker/window) ----------------

async def collect_records(
    pm: PortfolioManager,
    ticker: str,
    bars: pd.DataFrame,
    *,
    step: int = OPT_STEP,
    full_bars: Optional[pd.DataFrame] = None,
) -> list[EvalRecord]:
    """Walk-forward through bars, running agents at each step.
    Stores composite score + ATR -- NO trade simulation.
    This is the expensive step; runs exactly once per (ticker, window).
    Use step=STEP_BARS for the final accurate run, OPT_STEP for fast optimization.
    """
    records: list[EvalRecord] = []
    n = len(bars)

    # ── Pre-compute vol_ratio tables once (O(n)), avoids O(n²) groupby in loop.
    # Research #3: use full_bars for a stable 20-day daily-volume average.
    hist = full_bars if full_bars is not None else bars
    _h_dates = [ts.astimezone(_ET).date() for ts in hist.index]
    _h_date_s = pd.Series(_h_dates, index=hist.index, dtype=object)
    # daily total volume by date
    _daily_vol: dict = hist.groupby(_h_date_s)["volume"].sum().to_dict()
    _sorted_dates = sorted(_daily_vol.keys())
    # within-day cumulative volume per bar (inclusive)
    _tmp = hist[["volume"]].copy()
    _tmp["_d"] = _h_dates
    _tmp["_cv"] = _tmp.groupby("_d")["volume"].cumsum()
    _tmp["_bc"] = _tmp.groupby("_d").cumcount()  # 0-based bar count within day
    _cumvol: dict = _tmp["_cv"].to_dict()
    _barcnt: dict = _tmp["_bc"].to_dict()       # 0 = first bar of day

    def _vol_ratio_fast(entry_ts: pd.Timestamp, today_date) -> float:
        """O(1) projected-daily-vol / 20-day-avg just before entry_ts."""
        # Find last bar on today that precedes entry_ts
        loc = hist.index.searchsorted(entry_ts, side="left") - 1
        if loc < 0:
            return 1.0
        prev_ts = hist.index[loc]
        if _h_date_s.iloc[loc] != today_date:
            return 1.0   # no bars today yet
        today_vol = float(_cumvol.get(prev_ts, 0.0))
        bar_cnt   = int(_barcnt.get(prev_ts, 0)) + 1  # 1-based
        frac = min(bar_cnt / 78.0, 1.0)
        if frac < 0.05:
            return 1.0
        prior = [d for d in _sorted_dates if d < today_date]
        if not prior:
            return 1.0
        tail = prior[-20:]
        avg_daily = sum(_daily_vol[d] for d in tail) / len(tail)
        if avg_daily <= 0:
            return 1.0
        return (today_vol / frac) / avg_daily

    for i in range(LOOKBACK_BARS, n - 2, step):
        window        = bars.iloc[i - LOOKBACK_BARS: i]
        entry_bar_idx = i
        entry_ts      = bars.index[entry_bar_idx]

        # Skip last RTH hour (after 15:00 ET)
        if entry_ts.astimezone(timezone.utc).hour >= ENTRY_CUTOFF_UTC_HOUR:
            continue

        # Research #1 (PEAD): skip first 30 min of RTH open (9:30-9:59 ET).
        entry_et = entry_ts.astimezone(_ET)
        if entry_et.hour == 9:
            continue

        if entry_bar_idx + 1 >= n:
            continue

        # Research #3: session vol ratio (O(1) lookup via pre-computed tables)
        today_date = entry_et.date()
        vol_ratio  = _vol_ratio_fast(entry_ts, today_date)

        entry_price = float(bars["open"].iloc[entry_bar_idx + 1])

        ctx = AnalysisContext(
            ticker=ticker,
            bars=window,
            account={"equity": EQUITY, "buying_power": EQUITY * 0.5},
        )

        try:
            decision = await pm.decide(ctx)
        except Exception as exc:
            logger.debug("collect_records error %s@%s: %s", ticker, entry_ts, exc)
            continue

        atr = _atr14(window)
        if atr <= 0:
            continue

        records.append(EvalRecord(
            ticker        = ticker,
            entry_date_et = entry_ts.astimezone(_ET).date(),
            entry_ts      = entry_ts,
            entry_bar_idx = entry_bar_idx,
            composite     = float(decision.composite_score),
            atr           = atr,
            entry_price   = entry_price,
            lottery       = _is_lottery(window),
            vol_ratio     = vol_ratio,
        ))

    return records


# -- Fast replay (sync, no agent calls) ----------------------------------------

def replay_records(
    records: list[EvalRecord],
    bars: pd.DataFrame,
    params: dict[str, Any],
) -> list[TradeResult]:
    """Simulate trades from cached eval records with given params.
    Pure Python + pandas -- completes in milliseconds.
    """
    long_thr  = float(params["LONG_THRESHOLD"])
    short_thr = float(params["SHORT_THRESHOLD"])
    sl_mult   = float(params["ATR_STOP_MULTIPLE"])
    tp_mult   = float(params["ATR_TARGET_MULTIPLE"])
    rr        = tp_mult / sl_mult

    if rr < MIN_RR:
        return []

    results: list[TradeResult] = []
    sl_dates: set[date] = set()

    for rec in records:
        # Threshold filter
        if rec.composite >= long_thr:
            direction = Decision.LONG
        elif rec.composite <= short_thr:
            direction = Decision.SHORT
        else:
            continue

        # Same-day SL cooldown
        if rec.entry_date_et in sl_dates:
            continue

        atr   = rec.atr
        entry = rec.entry_price

        # Research #2 (CPT): tighten stop on lottery stocks.
        # If retail-frenzy profile detected, use 0.6× SL multiplier to survive
        # the violent mean-reversions that follow lottery-stock pumps.
        effective_sl_mult = sl_mult * 0.6 if rec.lottery else sl_mult
        sl_dist = atr * effective_sl_mult
        tp_dist = atr * tp_mult

        # Position sizing (matches RiskAgent.build_plan)
        qty = min(
            EQUITY * RISK_PCT / sl_dist,
            EQUITY * MAX_POS_PCT / entry,
        )
        qty = float(np.floor(qty))
        if qty <= 0:
            continue

        if direction is Decision.LONG:
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        future = bars.iloc[rec.entry_bar_idx + 1:]
        if future.empty:
            continue

        outcome, exit_px, exit_ts, pnl, pnl_pct = simulate_day_trade(
            future,
            direction=direction,
            entry=entry,
            stop_loss=sl,
            take_profit=tp,
            qty=qty,
            slippage_pct=SLIPPAGE_PCT,
        )

        results.append(TradeResult(
            ticker       = rec.ticker,
            direction    = direction.value,
            entry_time   = str(rec.entry_ts),
            exit_time    = exit_ts,
            entry_price  = round(entry, 4),
            exit_price   = round(exit_px, 4),
            qty          = qty,
            stop_loss    = round(sl, 4),
            take_profit  = round(tp, 4),
            risk_reward  = round(rr, 3),
            outcome      = outcome,
            pnl_usd      = round(pnl, 2),
            pnl_pct      = round(pnl_pct, 4),
            score        = round(rec.composite, 1),
        ))

        if outcome == "SL_HIT":
            sl_dates.add(rec.entry_date_et)

    return results


# -- Bar slicing ---------------------------------------------------------------

def _slice_bars(
    all_bars: dict[str, pd.DataFrame],
    days: int,
) -> dict[str, pd.DataFrame]:
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days + 3)
    return {
        sym: df[df.index >= cutoff]
        for sym, df in all_bars.items()
        if len(df[df.index >= cutoff]) >= LOOKBACK_BARS + 10
    }


# -- Phase 1: find best time window --------------------------------------------

async def phase1_window(
    all_bars: dict[str, pd.DataFrame],
    tickers: list[str],
    base_params: dict[str, Any],
    *,
    full_bars: dict[str, pd.DataFrame],
) -> tuple[int, dict[str, list[EvalRecord]], dict[str, pd.DataFrame]]:
    logger.info("=====  PHASE 1: Time-window search  =====")

    best_days    = TIME_WINDOWS[0]
    best_ev      = float("-inf")
    best_records: dict[str, list[EvalRecord]] = {}
    best_windowed: dict[str, pd.DataFrame]    = {}

    for days in TIME_WINDOWS:
        windowed = _slice_bars(all_bars, days)
        spy_bars = windowed.get("SPY")
        pm       = _build_pm(spy_bars)

        logger.info("  --- %d-day window: collecting agent scores (parallel) ---", days)
        records_by_ticker: dict[str, list[EvalRecord]] = {}
        t0 = time.time()

        valid_tickers = [t for t in tickers if t in windowed]
        # Run all tickers concurrently; pass full_bars for accurate vol_ratio history
        tasks = [
            collect_records(pm, ticker, windowed[ticker], full_bars=full_bars.get(ticker))
            for ticker in valid_tickers
        ]
        results_list = await asyncio.gather(*tasks)
        for ticker, recs in zip(valid_tickers, results_list):
            records_by_ticker[ticker] = recs
        logger.info("  Parallel collection done in %.1fs", time.time() - t0)

        total_recs = sum(len(v) for v in records_by_ticker.values())
        logger.info("  Collected %d eval points in %.1fs", total_recs, time.time() - t0)

        # Evaluate base params via fast replay
        all_trades: list[TradeResult] = []
        for ticker, recs in records_by_ticker.items():
            if ticker in windowed:
                all_trades.extend(replay_records(recs, windowed[ticker], base_params))

        s   = calc_summary(all_trades)
        n   = s.get("total_trades",  0)
        ev  = s.get("ev_per_trade",  float("-inf"))
        pf  = s.get("profit_factor", 0.0)
        wr  = s.get("win_rate",      0.0)
        pnl = s.get("total_pnl",     0.0)

        logger.info(
            "  %3d days | trades=%3d  EV/trade=$%+.2f  PF=%.2f  WR=%.1f%%  P&L=$%+.2f",
            days, n, ev, pf, wr, pnl,
        )

        if n >= MIN_TRADES and ev > best_ev:
            best_ev       = ev
            best_days     = days
            best_records  = records_by_ticker
            best_windowed = windowed

    logger.info("-> Best window: %d days  (EV/trade=$%+.2f)", best_days, best_ev)
    return best_days, best_records, best_windowed


# -- Phase 2: fast grid search -------------------------------------------------

def phase2_grid_fast(
    records_by_ticker: dict[str, list[EvalRecord]],
    windowed: dict[str, pd.DataFrame],
    best_days: int,
) -> tuple[dict[str, Any], dict, list]:
    logger.info("=====  PHASE 2: Parameter grid search  (%d-day window)  =====", best_days)

    keys   = list(PARAM_GRID.keys())
    combos = list(itertools.product(*PARAM_GRID.values()))
    total  = len(combos)
    logger.info("  Testing %d combos (in-memory replay -- no API calls)…", total)

    best_params: dict[str, Any] = {}
    best_ev     = float("-inf")
    best_stats: dict = {}
    leaderboard: list[tuple] = []
    prog = Progress(total, "  Phase2")

    t0 = time.time()
    for idx, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))

        all_trades: list[TradeResult] = []
        for ticker, recs in records_by_ticker.items():
            if ticker in windowed:
                all_trades.extend(replay_records(recs, windowed[ticker], params))

        s   = calc_summary(all_trades)
        n   = s.get("total_trades",  0)
        ev  = s.get("ev_per_trade",  float("-inf"))
        pf  = s.get("profit_factor", 0.0)
        wr  = s.get("win_rate",      0.0)
        pnl = s.get("total_pnl",     0.0)

        if n >= MIN_TRADES and ev > best_ev:
            best_ev     = ev
            best_params = params
            best_stats  = s

        leaderboard.append((ev, pf, wr, pnl, n, params))
        prog.update(idx, f"best EV=${best_ev:+.2f}")

    prog.done()
    elapsed = time.time() - t0
    logger.info("  Grid done in %.1fs  (%.0f ms/combo)", elapsed, elapsed / total * 1000)

    # Top-5 configs
    leaderboard.sort(key=lambda x: x[0], reverse=True)
    logger.info("  --- Top 5 configs ---")
    for rank, (ev, pf, wr, pnl, n, p) in enumerate(leaderboard[:5], 1):
        rr = p["ATR_TARGET_MULTIPLE"] / p["ATR_STOP_MULTIPLE"]
        logger.info(
            "  #%d  EV=$%+.2f  PF=%.2f  WR=%.1f%%  P&L=$%+.2f  trades=%d",
            rank, ev, pf, wr, pnl, n,
        )
        logger.info(
            "      LONG=%g  SHORT=%g  SL=%.1fx  TP=%.1fx  RR=%.2f",
            p["LONG_THRESHOLD"], p["SHORT_THRESHOLD"],
            p["ATR_STOP_MULTIPLE"], p["ATR_TARGET_MULTIPLE"], rr,
        )

    if best_params:
        logger.info(
            "-> Best: LONG=%g  SHORT=%g  SL=%.1fx  TP=%.1fx  "
            "EV=$%+.2f  trades=%d",
            best_params["LONG_THRESHOLD"], best_params["SHORT_THRESHOLD"],
            best_params["ATR_STOP_MULTIPLE"], best_params["ATR_TARGET_MULTIPLE"],
            best_ev, best_stats.get("total_trades", 0),
        )
    else:
        logger.warning("No valid config found with >= %d trades", MIN_TRADES)

    return best_params, best_stats, leaderboard


# -- Walk-forward out-of-sample validation -------------------------------------

def _global_cutoff_date(records_by_ticker: dict[str, list], oos_frac: float) -> Optional[date]:
    """Date at the (1 - oos_frac) quantile across ALL records' entry dates.

    A single global cutoff keeps the in-sample / out-of-sample split temporally
    clean across every ticker (no look-ahead: OOS is strictly later in time).
    """
    all_dates = sorted(
        rec.entry_date_et for recs in records_by_ticker.values() for rec in recs
    )
    if not all_dates:
        return None
    idx = int(len(all_dates) * (1.0 - oos_frac))
    idx = max(0, min(idx, len(all_dates) - 1))
    return all_dates[idx]


def _split_records_by_date(
    records_by_ticker: dict[str, list], cutoff: date,
) -> tuple[dict[str, list], dict[str, list]]:
    """Partition each ticker's records into (in_sample < cutoff, oos >= cutoff)."""
    in_sample: dict[str, list] = {}
    out_sample: dict[str, list] = {}
    for ticker, recs in records_by_ticker.items():
        in_sample[ticker]  = [r for r in recs if r.entry_date_et <  cutoff]
        out_sample[ticker] = [r for r in recs if r.entry_date_et >= cutoff]
    return in_sample, out_sample


def walk_forward_validate(
    records_by_ticker: dict[str, list],
    windowed: dict[str, pd.DataFrame],
    best_days: int,
    *,
    oos_frac: float = 0.3,
) -> Optional[dict]:
    """Honest validation: pick params on in-sample data, REPORT on out-of-sample.

    The default optimizer selects the best of 96 configs on the full window and
    reports that same window — guaranteed-optimistic (it fits noise). Here we
    select on the earlier (1-oos_frac) of the data and measure the chosen config
    on the held-out later oos_frac it never saw. A large IS→OOS degradation is
    the signature of overfitting; similar IS/OOS numbers mean the edge is real.
    """
    cutoff = _global_cutoff_date(records_by_ticker, oos_frac)
    if cutoff is None:
        logger.warning("walk-forward: no records to split")
        return None

    is_records, oos_records = _split_records_by_date(records_by_ticker, cutoff)
    n_is  = sum(len(v) for v in is_records.values())
    n_oos = sum(len(v) for v in oos_records.values())
    logger.info("=====  WALK-FORWARD VALIDATION  =====")
    logger.info("  Split @ %s  |  in-sample eval points=%d  out-of-sample=%d",
                cutoff, n_is, n_oos)
    if n_is < MIN_TRADES or n_oos < MIN_TRADES:
        logger.warning("  Too few points either side of the split — results unreliable")

    # 1. Select the winning config on IN-SAMPLE only.
    best_params, is_stats, _ = phase2_grid_fast(is_records, windowed, best_days)
    if not best_params:
        logger.warning("  walk-forward: no valid in-sample config")
        return None

    # 2. Apply that frozen config to OUT-OF-SAMPLE data it never influenced.
    oos_trades: list[TradeResult] = []
    for ticker, recs in oos_records.items():
        if ticker in windowed:
            oos_trades.extend(replay_records(recs, windowed[ticker], best_params))
    oos_stats = calc_summary(oos_trades)

    def _g(s: dict, k: str, d=0.0):
        return s.get(k, d) if s else d

    logger.info("  ---  IN-SAMPLE (param selection)  vs  OUT-OF-SAMPLE (held out)  ---")
    logger.info("  metric            in-sample     out-of-sample")
    logger.info("  trades            %9d     %9d",
                _g(is_stats, "total_trades"), _g(oos_stats, "total_trades"))
    logger.info("  win_rate          %8.1f%%     %8.1f%%",
                _g(is_stats, "win_rate"), _g(oos_stats, "win_rate"))
    logger.info("  EV/trade          %+9.2f     %+9.2f",
                _g(is_stats, "ev_per_trade"), _g(oos_stats, "ev_per_trade"))
    logger.info("  profit_factor     %9.2f     %9.2f",
                _g(is_stats, "profit_factor"), _g(oos_stats, "profit_factor"))

    is_ev  = _g(is_stats, "ev_per_trade")
    oos_ev = _g(oos_stats, "ev_per_trade")
    if oos_ev <= 0 < is_ev:
        logger.warning("  ⚠ OVERFIT: positive in-sample EV does NOT survive out-of-sample.")
    elif is_ev > 0 and oos_ev >= is_ev * 0.5:
        logger.info("  ✓ Edge holds out-of-sample (OOS EV >= 50%% of in-sample).")
    else:
        logger.info("  ~ Partial degradation — treat the live edge as the OOS number.")

    return {
        "cutoff_date":   str(cutoff),
        "oos_frac":      oos_frac,
        "selected_on":   "in_sample",
        "params":        best_params,
        "in_sample":     is_stats,
        "out_of_sample": oos_stats,
    }


# -- Phase 3: final summary + save results -------------------------------------

async def phase3_final(
    records_by_ticker: dict[str, list[EvalRecord]],
    windowed: dict[str, pd.DataFrame],
    best_params: dict[str, Any],
    best_days: int,
    *,
    full_bars: Optional[dict[str, pd.DataFrame]] = None,
) -> None:
    logger.info("=====  PHASE 3: Final run with optimal settings  =====")

    if not best_params:
        logger.error("No best params -- aborting phase 3")
        return

    # Re-collect with fine step (STEP_BARS) for the final accurate run
    logger.info("  Re-collecting at full resolution (STEP_BARS=%d)...", STEP_BARS)
    spy_bars = windowed.get("SPY")
    pm_final = _build_pm(spy_bars)
    _fb = full_bars or {}
    tasks = [
        collect_records(pm_final, ticker, windowed[ticker], step=STEP_BARS,
                        full_bars=_fb.get(ticker))
        for ticker in records_by_ticker
        if ticker in windowed
    ]
    fine_results = await asyncio.gather(*tasks)
    fine_records = dict(zip(
        [t for t in records_by_ticker if t in windowed],
        fine_results,
    ))
    total_fine = sum(len(v) for v in fine_records.values())
    logger.info("  Fine collection: %d eval points", total_fine)

    all_trades: list[TradeResult] = []
    for ticker, recs in fine_records.items():
        if ticker not in windowed:
            continue
        trades = replay_records(recs, windowed[ticker], best_params)
        pnl    = sum(t.pnl_usd for t in trades)
        logger.info("  %s: %d trades  P&L=$%.2f", ticker, len(trades), pnl)
        all_trades.extend(trades)

    if not all_trades:
        logger.warning("No trades in final run -- thresholds may be too tight")
        return

    summary = calc_summary(all_trades)

    summary["optimal_params"]      = best_params
    summary["optimal_window_days"] = best_days

    RESULTS_FILE.write_text(json.dumps(summary, indent=2, default=str))
    logger.info("Saved: %s", RESULTS_FILE)

    rr = best_params["ATR_TARGET_MULTIPLE"] / best_params["ATR_STOP_MULTIPLE"]
    config_text = (
        f"# Optimal backtest configuration\n"
        f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"# Window: {best_days} days\n\n"
        f"LONG_THRESHOLD={best_params['LONG_THRESHOLD']}\n"
        f"SHORT_THRESHOLD={best_params['SHORT_THRESHOLD']}\n"
        f"ATR_STOP_MULTIPLE={best_params['ATR_STOP_MULTIPLE']}\n"
        f"ATR_TARGET_MULTIPLE={best_params['ATR_TARGET_MULTIPLE']}\n"
        f"\n# Derived\n"
        f"# R:R ratio:       {rr:.2f}  (break-even at {1/(1+rr)*100:.1f}% win rate)\n"
        f"\n# Backtest results\n"
        f"# Trades:          {summary['total_trades']}\n"
        f"# Win rate:        {summary['win_rate']:.1f}%\n"
        f"# EV/trade:        ${summary['ev_per_trade']:+.2f}\n"
        f"# Profit factor:   {summary['profit_factor']:.2f}\n"
        f"# Sharpe ratio:    {summary['sharpe']:.2f}\n"
        f"# Max drawdown:    ${summary['max_drawdown']:.2f}\n"
        f"# Total P&L:       ${summary['total_pnl']:+.2f}\n"
    )
    CONFIG_FILE.write_text(config_text)
    logger.info("Saved: %s", CONFIG_FILE)

    _update_env(best_params)

    # Print human-readable table (may fail on non-UTF-8 terminals -- harmless)
    try:
        print_summary(all_trades)
    except Exception:
        pass


def _update_env(params: dict[str, Any]) -> None:
    """Patch relevant lines in .env, leave everything else untouched."""
    if not ENV_FILE.exists():
        logger.warning(".env not found at %s -- skipping update", ENV_FILE)
        return

    lines     = ENV_FILE.read_text().splitlines(keepends=True)
    keys_seen: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        replaced = False
        for key, val in params.items():
            if line.startswith(f"{key}="):
                new_lines.append(f"{key}={val}\n")
                keys_seen.add(key)
                replaced = True
                break
        if not replaced:
            new_lines.append(line)

    for key, val in params.items():
        if key not in keys_seen:
            new_lines.append(f"{key}={val}\n")

    ENV_FILE.write_text("".join(new_lines))
    logger.info(".env updated:")
    for k, v in params.items():
        logger.info("  %s = %s", k, v)


# -- Main ----------------------------------------------------------------------

async def run(tickers: list[str], *, walk_forward: bool = False, oos_frac: float = 0.3) -> None:
    settings = load_settings()

    if not settings.alpaca_key_id or not settings.alpaca_secret:
        logger.error("ALPACA_API_KEY_ID / ALPACA_API_SECRET not set in .env")
        return

    fetch_list = list(dict.fromkeys(tickers + ["SPY"]))
    max_days   = max(TIME_WINDOWS) + 5

    end_dt   = datetime.now(tz=timezone.utc).replace(hour=23, minute=59, second=59)
    start_dt = end_dt - timedelta(days=max_days)

    logger.info("Fetching %d days of bars for %d symbols...", max_days, len(fetch_list))

    prog = Progress(len(fetch_list), "  Fetch")
    all_bars: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(fetch_list, 1):
        df = await fetch_bars_range(
            sym, start_dt, end_dt,
            settings.alpaca_key_id, settings.alpaca_secret,
        )
        if df is not None and len(df) >= LOOKBACK_BARS + 10:
            all_bars[sym] = df
        else:
            logger.warning("  %s: skipped (insufficient data)", sym)
        prog.update(i)
    prog.done()

    logger.info("Fetched bars for: %s", " ".join(sorted(all_bars.keys())))

    base_params: dict[str, Any] = {
        "LONG_THRESHOLD":      65,
        "SHORT_THRESHOLD":     35,
        "ATR_STOP_MULTIPLE":   2.0,
        "ATR_TARGET_MULTIPLE": 4.0,
    }

    best_days, best_records, best_windowed = await phase1_window(
        all_bars, tickers, base_params,
        full_bars=all_bars,
    )

    if walk_forward:
        wf = walk_forward_validate(best_records, best_windowed, best_days, oos_frac=oos_frac)
        if wf:
            (_here.parent / "backtest_walkforward.json").write_text(
                json.dumps(wf, indent=2, default=str)
            )
            logger.info("Saved: %s", _here.parent / "backtest_walkforward.json")
        logger.info("Walk-forward complete. (no .env written — this mode only measures)")
        return

    best_params, best_stats, _ = phase2_grid_fast(
        best_records, best_windowed, best_days,
    )

    if not best_params:
        logger.warning("Grid found no valid config; falling back to base params")
        best_params = base_params

    await phase3_final(best_records, best_windowed, best_params, best_days,
                       full_bars=all_bars)

    logger.info("Optimizer complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fast backtest optimizer")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--walk-forward", action="store_true",
                        help="Select params in-sample, REPORT out-of-sample "
                             "(honest validation; does not write .env)")
    parser.add_argument("--oos-frac", type=float, default=0.3,
                        help="Fraction of the window held out for out-of-sample (default 0.3)")
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers if t.upper() != "SPY"]
    n_combos = len(list(itertools.product(*PARAM_GRID.values())))
    logger.info("Optimizer starting  tickers: %s", " ".join(tickers))
    logger.info("Search: %d time windows x %d param combos",
                len(TIME_WINDOWS), n_combos)
    if args.walk_forward:
        logger.info("Mode: WALK-FORWARD (out-of-sample validation, %.0f%% held out)",
                    args.oos_frac * 100)

    asyncio.run(run(tickers, walk_forward=args.walk_forward, oos_frac=args.oos_frac))


if __name__ == "__main__":
    main()
