"""Strategy parameter optimizer — grid search over key thresholds and ATR multiples.

Usage (from trading_bot/ directory):
    python optimize_strategy.py
    python optimize_strategy.py --days 14 --tickers NVDA TSLA AAPL MSFT AMD
    python optimize_strategy.py --days 14 --phase thresholds
    python optimize_strategy.py --days 14 --phase atr

Two-phase search:
  Phase 1 (thresholds): Grid over LONG_THRESHOLD × SHORT_THRESHOLD.
  Phase 2 (atr):        Grid over ATR_STOP_MULTIPLE × ATR_TARGET_MULTIPLE
                        using the best thresholds found in Phase 1.

Bars are fetched ONCE and reused across all parameter combos — ~10× faster
than re-fetching per combination.

Results written to optimization_results.json (ranked by Sharpe).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

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

sys.path.insert(0, str(_here))

from config.settings import load_settings, Settings, AgentWeights, RiskConfig, DecisionThresholds
from core.enums import Decision
from backtest_30day import (
    fetch_bars_range,
    backtest_ticker,
    calc_summary,
    SLIPPAGE_PCT,
)
from bootstrap import build_manager

logging.basicConfig(
    level=logging.WARNING,           # suppress agent noise during grid search
    format="%(asctime)s %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("opt")
logging.getLogger("opt").setLevel(logging.INFO)

RESULTS_FILE = _here.parent / "optimization_results.json"
MIN_TRADES   = 20   # discard combos with fewer trades (not statistically meaningful)

# ── Parameter grids ────────────────────────────────────────────────────────────

THRESHOLD_GRID = {
    "LONG_THRESHOLD":  [55.0, 58.0, 60.0, 63.0, 66.0],
    "SHORT_THRESHOLD": [34.0, 37.0, 40.0, 43.0, 46.0],
}

ATR_GRID = {
    "ATR_STOP_MULTIPLE":   [1.5, 2.0, 2.5, 3.0],
    "ATR_TARGET_MULTIPLE": [2.5, 3.0, 4.0, 5.0],
}

DEFAULT_TICKERS = ["NVDA", "TSLA", "AAPL", "MSFT", "AMD", "META", "AMZN"]


# ── Settings factory ───────────────────────────────────────────────────────────

def _make_settings(overrides: dict) -> Settings:
    """Clone current settings with env var overrides applied."""
    for k, v in overrides.items():
        os.environ[k] = str(v)
    s = load_settings()
    # Clean up so next call starts fresh
    for k in overrides:
        os.environ.pop(k, None)
    return s


# ── Single-combo evaluator ─────────────────────────────────────────────────────

async def _eval_combo(
    params: dict,
    tickers: list[str],
    bars_cache: dict[str, pd.DataFrame],
    spy_bars: Optional[pd.DataFrame],
) -> dict:
    """Run the full backtest for one parameter combination; return summary dict."""
    settings = _make_settings(params)
    pm = build_manager(
        settings,
        broker=None,
        include_live_only_agents=False,
        include_vision=False,
        include_decision_agent=False,
        include_insider=False,
        include_squeeze=False,
    )

    all_trades = []
    for ticker in tickers:
        bars = bars_cache.get(ticker)
        if bars is None or bars.empty:
            continue
        try:
            trades = await backtest_ticker(pm, ticker, bars, spy_bars)
            all_trades.extend(trades)
        except Exception as exc:
            logger.warning("backtest_ticker error %s: %s", ticker, exc)

    summary = calc_summary(all_trades)
    summary["params"] = params
    return summary


# ── Data fetch ─────────────────────────────────────────────────────────────────

async def _fetch_all(
    tickers: list[str],
    days: int,
    key_id: str,
    secret: str,
) -> tuple[dict[str, pd.DataFrame], Optional[pd.DataFrame]]:
    end_dt   = datetime.now(tz=timezone.utc).replace(hour=23, minute=59, second=59)
    start_dt = end_dt - timedelta(days=days + 5)

    all_syms = list(set(tickers + ["SPY"]))
    logger.info("Fetching bars for: %s  (%d days)", all_syms, days)

    tasks = {
        sym: fetch_bars_range(sym, start_dt, end_dt, key_id, secret)
        for sym in all_syms
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    bars_map: dict[str, pd.DataFrame] = {}
    for sym, res in zip(tasks.keys(), results):
        if isinstance(res, Exception):
            logger.warning("Fetch error %s: %s", sym, res)
        elif res is not None:
            bars_map[sym] = res

    spy_bars = bars_map.pop("SPY", None)
    return bars_map, spy_bars


# ── Grid runners ───────────────────────────────────────────────────────────────

async def _run_threshold_grid(
    tickers: list[str],
    bars_cache: dict,
    spy_bars,
    fixed_atr_stop: float = 2.0,
    fixed_atr_target: float = 4.0,
) -> list[dict]:
    keys   = list(THRESHOLD_GRID.keys())
    values = list(THRESHOLD_GRID.values())
    combos = [dict(zip(keys, v)) for v in product(*values)]
    # Only test symmetric or asymmetric combos where gap >= 10 pts
    combos = [c for c in combos if c["LONG_THRESHOLD"] - c["SHORT_THRESHOLD"] >= 10]

    logger.info("Phase 1 — threshold grid: %d combos", len(combos))
    results = []
    for i, combo in enumerate(combos, 1):
        params = {
            **combo,
            "ATR_STOP_MULTIPLE":   fixed_atr_stop,
            "ATR_TARGET_MULTIPLE": fixed_atr_target,
        }
        logger.info("  [%d/%d] %s", i, len(combos), _fmt_params(params))
        summary = await _eval_combo(params, tickers, bars_cache, spy_bars)
        if summary.get("total_trades", 0) >= MIN_TRADES:
            results.append(summary)
        else:
            logger.info("    → skipped (only %d trades)", summary.get("total_trades", 0))

    return sorted(results, key=lambda r: r.get("sharpe", -99), reverse=True)


async def _run_atr_grid(
    tickers: list[str],
    bars_cache: dict,
    spy_bars,
    best_long_thresh: float,
    best_short_thresh: float,
) -> list[dict]:
    keys   = list(ATR_GRID.keys())
    values = list(ATR_GRID.values())
    combos = [dict(zip(keys, v)) for v in product(*values)]
    # Only test combos where target > stop (sensible R/R)
    combos = [c for c in combos if c["ATR_TARGET_MULTIPLE"] > c["ATR_STOP_MULTIPLE"]]

    logger.info("Phase 2 — ATR grid: %d combos (thresholds fixed at L=%.0f S=%.0f)",
                len(combos), best_long_thresh, best_short_thresh)
    results = []
    for i, combo in enumerate(combos, 1):
        params = {
            **combo,
            "LONG_THRESHOLD":  best_long_thresh,
            "SHORT_THRESHOLD": best_short_thresh,
        }
        logger.info("  [%d/%d] %s", i, len(combos), _fmt_params(params))
        summary = await _eval_combo(params, tickers, bars_cache, spy_bars)
        if summary.get("total_trades", 0) >= MIN_TRADES:
            results.append(summary)
        else:
            logger.info("    → skipped (only %d trades)", summary.get("total_trades", 0))

    return sorted(results, key=lambda r: r.get("sharpe", -99), reverse=True)


# ── Output helpers ─────────────────────────────────────────────────────────────

def _fmt_params(p: dict) -> str:
    return "  ".join(f"{k}={v}" for k, v in p.items())


def _print_table(results: list[dict], title: str) -> None:
    if not results:
        print(f"\n  {title}: no results met the minimum trade threshold.\n")
        return

    print(f"\n{'=' * 76}")
    print(f"  {title}")
    print(f"{'=' * 76}")
    hdr = f"  {'Params':<40} {'Trades':>6} {'WinR%':>6} {'Sharpe':>7} {'EV/T':>8} {'PF':>6} {'MaxDD':>8}"
    print(hdr)
    print(f"  {'-' * 74}")

    for r in results[:15]:          # top 15
        p     = r.get("params", {})
        label = "  ".join(f"{k.replace('_THRESHOLD','_T').replace('_MULTIPLE','_M')}={v}"
                          for k, v in p.items())
        print(
            f"  {label:<40} "
            f"{r.get('total_trades', 0):>6} "
            f"{r.get('win_rate', 0):>5.1f}% "
            f"{r.get('sharpe', 0):>7.3f} "
            f"${r.get('ev_per_trade', 0):>7.2f} "
            f"{r.get('profit_factor', 0):>6.2f} "
            f"${r.get('max_drawdown', 0):>7.2f}"
        )
    print(f"{'=' * 76}\n")


def _print_recommendation(thresh_results: list[dict], atr_results: list[dict]) -> None:
    if not thresh_results and not atr_results:
        return

    print("=" * 76)
    print("  RECOMMENDATION")
    print("=" * 76)

    best = (atr_results or thresh_results)[0]
    p = best.get("params", {})
    print("\n  Best parameter set (by Sharpe):")
    for k, v in p.items():
        cur = {
            "LONG_THRESHOLD": 60.0, "SHORT_THRESHOLD": 40.0,
            "ATR_STOP_MULTIPLE": 2.0, "ATR_TARGET_MULTIPLE": 4.0,
        }.get(k, "?")
        arrow = "↑" if float(v) > float(cur) else "↓" if float(v) < float(cur) else "="
        print(f"    {k:<26} {cur} → {v}  {arrow}")

    print(f"\n  Stats:  Sharpe={best.get('sharpe'):.3f}  "
          f"EV/trade=${best.get('ev_per_trade'):.2f}  "
          f"WinRate={best.get('win_rate'):.1f}%  "
          f"PF={best.get('profit_factor'):.2f}  "
          f"Trades={best.get('total_trades')}")

    print("\n  To apply, add to Railway env vars (or .env):")
    for k, v in p.items():
        print(f"    {k}={v}")
    print("=" * 76 + "\n")


# ── Main ───────────────────────────────────────────────────────────────────────

async def run(tickers: list[str], days: int, phase: str) -> None:
    settings = load_settings()
    if not settings.alpaca_key_id or not settings.alpaca_secret:
        logger.error("ALPACA_API_KEY_ID and ALPACA_API_SECRET must be set in .env or environment")
        return

    print(f"\nOptimizer — {days}d window, {len(tickers)} tickers: {tickers}")
    print(f"Phase: {phase}  |  Min trades: {MIN_TRADES}  |  Slippage: {SLIPPAGE_PCT*100:.3f}%\n")

    bars_cache, spy_bars = await _fetch_all(tickers, days, settings.alpaca_key_id, settings.alpaca_secret)
    if not bars_cache:
        logger.error("No bars fetched — check API keys and market hours")
        return

    thresh_results: list[dict] = []
    atr_results:    list[dict] = []

    if phase in ("thresholds", "both"):
        thresh_results = await _run_threshold_grid(tickers, bars_cache, spy_bars)
        _print_table(thresh_results, "Phase 1 — Threshold Grid (ranked by Sharpe)")

    best_long  = thresh_results[0]["params"]["LONG_THRESHOLD"]  if thresh_results else 60.0
    best_short = thresh_results[0]["params"]["SHORT_THRESHOLD"] if thresh_results else 40.0

    if phase in ("atr", "both"):
        atr_results = await _run_atr_grid(tickers, bars_cache, spy_bars, best_long, best_short)
        _print_table(atr_results, "Phase 2 — ATR Grid (ranked by Sharpe)")

    _print_recommendation(thresh_results, atr_results)

    all_results = thresh_results + atr_results
    for r in all_results:
        r.pop("trades", None)        # don't bloat the output file
    RESULTS_FILE.write_text(json.dumps({
        "run_at":          datetime.utcnow().isoformat(),
        "tickers":         tickers,
        "days":            days,
        "threshold_grid":  thresh_results[:10],
        "atr_grid":        atr_results[:10],
        "best":            (atr_results or thresh_results or [{}])[0],
    }, indent=2))
    print(f"Full results saved to {RESULTS_FILE}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Trading strategy optimizer")
    parser.add_argument("--days",    type=int, default=14,
                        help="Lookback window in days (default 14)")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS,
                        help="Tickers to backtest")
    parser.add_argument("--phase",   choices=["thresholds", "atr", "both"],
                        default="both",
                        help="Which grid to search (default: both)")
    args = parser.parse_args()
    asyncio.run(run(args.tickers, args.days, args.phase))


if __name__ == "__main__":
    main()
