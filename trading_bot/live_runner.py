"""Live runner.

Replaces the one-shot polling loop in main.py for LIVE mode.
Instead of evaluating tickers on a fixed interval, the bot:

  1. Auto-scans the market for candidates (most-active + gainers/losers)
     OR accepts an explicit ticker list from the CLI.
  2. Detects the macro regime + injects SPY bars (refreshed every rescan).
  3. Runs an initial evaluation of all candidates on startup.
  4. Re-scans the market universe every RESCAN_INTERVAL_MIN minutes and
     refreshes the active ticker list.
  5. Flattens all positions shortly before the 16:00 ET close (EOD_FLATTEN).

Usage:
    python live_runner.py              # auto-scan (no args needed)
    python live_runner.py AAPL NVDA   # manual override
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, time as _time
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo


import bootstrap  # loads .env files on import — keep first
from bootstrap import (
    build_broker, build_manager, eod_flatten_loop, eod_report_loop,
    refresh_market_context,
)
from config.settings import load_settings
from core.models import AnalysisContext
from data.chart_renderer import render_chart
from data.universe_scanner import UniverseScanner
from execution.base_broker import BaseBroker
from execution.portfolio_manager import PortfolioManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("live")

_ET = ZoneInfo("America/New_York")

RESCAN_INTERVAL_MIN    = int(os.environ.get("RESCAN_INTERVAL_MIN",    "30"))
BREAKOUT_INTERVAL_MIN  = int(os.environ.get("BREAKOUT_INTERVAL_MIN",  "5"))
BREAKOUT_MIN_CHANGE    = float(os.environ.get("BREAKOUT_MIN_CHANGE_PCT", "3.0"))
STRATEGY_REFRESH_MIN   = int(os.environ.get("STRATEGY_REFRESH_MIN",   "60"))

# Weights file written by api_server's self-tuner — we read it hourly and
# apply ATR/threshold updates to the live pm without restarting.
_WEIGHTS_FILE = Path(__file__).parent / "data" / "strategy_weights.json"

# Auto-execute toggle written by the dashboard (/api/trade-mode). Read every
# evaluation so the switch takes effect within one scan, no redeploy needed.
_TRADE_MODE_FILE = Path(__file__).parent / "data" / "trade_mode.json"


def _auto_execute_enabled() -> bool:
    """Whether the dashboard has enabled autonomous order execution.

    Default False (manual approval): the bot still scores every ticker and
    publishes signals, but places NO orders itself — the user authorizes each
    trade from the dashboard's Trade Recommendations page. Returns True only
    when the user has flipped the toggle to auto-execute.
    """
    try:
        if _TRADE_MODE_FILE.exists():
            data = json.loads(_TRADE_MODE_FILE.read_text())
            return bool(data.get("auto_execute", False))
    except Exception:
        pass
    return False

# Used when the universe scanner is unreachable at startup (network blip):
# deep-liquidity names so the bot stays alive until the scanner recovers.
FALLBACK_WATCHLIST = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA"]

# Core names always included — highest liquidity + consistent intraday range.
# The universe scanner adds momentum movers on top of these.
CORE_WATCHLIST = os.environ.get("CORE_WATCHLIST", "NVDA,TSLA,AAPL,MSFT,AMD,META,AMZN,GOOGL").split(",")

# Protects concurrent reads/writes to the active_tickers list across async tasks.
_ticker_lock: asyncio.Lock | None = None

# Caps concurrent ticker evaluations to avoid Alpaca API rate limit exhaustion.
_EVAL_SEMAPHORE: asyncio.Semaphore | None = None


def _is_market_hours() -> bool:
    """True during US equities regular session (Mon–Fri 09:30–16:10 ET)."""
    now = datetime.now(_ET)
    return (
        now.weekday() < 5
        and _time(9, 30) <= now.time() <= _time(16, 10)
    )


async def evaluate_ticker(
    pm: PortfolioManager,
    broker: BaseBroker,
    ticker: str,
    *,
    execute: bool,
) -> None:
    async with _EVAL_SEMAPHORE:
        try:
            # Fetch 5-min and 1-hour bars concurrently; hourly enables MTF gate
            raw = await asyncio.gather(
                broker.get_bars(ticker, timeframe="5Min", limit=200),
                broker.get_bars(ticker, timeframe="1Hour", limit=60),
                return_exceptions=True,
            )
            bars        = raw[0] if not isinstance(raw[0], Exception) else None
            hourly_bars = raw[1] if not isinstance(raw[1], Exception) else None
            account = await broker.get_account()
            chart = render_chart(ticker, bars)
            ctx = AnalysisContext(
                ticker=ticker, bars=bars, account=account,
                chart_image_path=chart, hourly_bars=hourly_bars,
            )
            # Auto-execute only when BOTH the deploy allows live orders
            # (EXECUTE_LIVE=true) AND the user has enabled auto-execute on the
            # site. Otherwise score & publish the signal for manual approval.
            effective_execute = execute and _auto_execute_enabled()
            decision = await pm.run_once(ctx, execute=effective_execute)
            logger.info(
                "%s -> %s | composite=%.1f | mode=%s | %s",
                ticker, decision.decision.value, decision.composite_score,
                "AUTO" if effective_execute else "MANUAL",
                pm.summarise(decision.evaluations),
            )
        except Exception:
            logger.exception("evaluation failed for %s", ticker)


async def breakout_monitor_loop(
    pm: PortfolioManager,
    broker: BaseBroker,
    active_tickers: list[str],
    *,
    execute: bool,
    interval_min: int,
    universe: UniverseScanner,
    min_change_pct: float,
) -> None:
    """Poll for sudden movers not in the active watchlist every interval_min minutes.

    Catches news-driven spikes and halts-resuming that the 30-min full rescan
    would otherwise miss by up to half an hour. Any breakout found is added to
    active_tickers (so subsequent heartbeats can re-trigger it) and evaluated
    immediately with the full agent pipeline.
    """
    while True:
        await asyncio.sleep(interval_min * 60)
        try:
            breakouts = await universe.get_breakouts(
                existing_tickers={t.upper() for t in active_tickers},
                min_change_pct=min_change_pct,
            )
            if not breakouts:
                continue
            await pm.refresh_protections()
            async with _ticker_lock:
                for sym in breakouts:
                    if sym not in active_tickers:
                        active_tickers.append(sym)
            logger.info("BREAKOUT ALERT: %s — evaluating immediately", breakouts)
            await asyncio.gather(
                *[evaluate_ticker(pm, broker, t, execute=execute)
                  for t in breakouts],
                return_exceptions=True,
            )
        except Exception:
            logger.exception("breakout monitor error")


async def rescan_loop(
    pm: PortfolioManager,
    broker: BaseBroker,
    active_tickers: list[str],
    *,
    execute: bool,
    interval_min: int,
    universe: UniverseScanner | None = None,
    scanner_cfg=None,
) -> None:
    """Refresh market context and re-evaluate tickers every interval_min minutes.

    Mutates ``active_tickers`` in place so the heartbeat handler always sees
    the current universe.
    """
    while True:
        await asyncio.sleep(interval_min * 60)

        if universe is not None and scanner_cfg is not None:
            try:
                fresh = await universe.get_candidates(
                    top_n=scanner_cfg.top_n,
                    min_price=scanner_cfg.min_price,
                    max_price=scanner_cfg.max_price,
                    min_volume=scanner_cfg.min_volume,
                    min_change=scanner_cfg.min_change_pct,
                )
                if fresh:
                    async with _ticker_lock:
                        added   = set(fresh) - set(active_tickers)
                        removed = set(active_tickers) - set(fresh)
                        active_tickers[:] = fresh
                    if added or removed:
                        logger.info(
                            "Universe refreshed: +%s -%s -> active=%s",
                            sorted(added), sorted(removed), active_tickers
                        )
            except Exception:
                logger.exception("Universe refresh failed -- keeping previous list")

        # Regime + SPY bars go stale over a session — refresh before re-scoring.
        await refresh_market_context(pm, broker)
        # Detect exits → update re-entry cooldowns, loss-streak guard, memory.
        await pm.refresh_protections()

        if not _is_market_hours():
            logger.debug("rescan: market closed — skipping LLM evaluation")
            continue

        async with _ticker_lock:
            snapshot = list(active_tickers)
        logger.info("Scheduled rescan of %s", snapshot)
        await asyncio.gather(
            *[evaluate_ticker(pm, broker, t, execute=execute)
              for t in snapshot],
            return_exceptions=True,
        )


async def strategy_refresh_loop(pm: PortfolioManager, *, interval_min: int) -> None:
    """Apply api_server's self-tuned strategy weights to the live PortfolioManager.

    api_server._update_strategy_weights() adjusts ATR multiples based on
    the last 20 closed trades (win rate) and writes them to strategy_weights.json.
    This loop reads that file hourly and pushes the updates into the live pm
    so the bot continuously improves without a restart.
    """
    while True:
        await asyncio.sleep(interval_min * 60)
        try:
            if not _WEIGHTS_FILE.exists():
                continue
            w = json.loads(_WEIGHTS_FILE.read_text())
            stop_m = w.get("atr_stop_multiple")
            tp_m   = w.get("atr_target_multiple")
            changed = False
            if stop_m and abs(pm.risk.cfg.atr_stop_multiple - float(stop_m)) > 1e-6:
                pm.risk.cfg.atr_stop_multiple = float(stop_m)
                changed = True
            if tp_m and abs(pm.risk.cfg.atr_target_multiple - float(tp_m)) > 1e-6:
                pm.risk.cfg.atr_target_multiple = float(tp_m)
                changed = True
            if changed:
                logger.info(
                    "Strategy v%s applied: atr_stop=%.2f atr_tp=%.2f "
                    "bias=%s win_rate=%.1f%%",
                    w.get("update_count", "?"),
                    pm.risk.cfg.atr_stop_multiple,
                    pm.risk.cfg.atr_target_multiple,
                    w.get("bias", "?"),
                    w.get("win_rate_30d") or 0.0,
                )
        except Exception:
            logger.exception("strategy refresh failed")


async def breakeven_lock_loop(broker: BaseBroker, pm: PortfolioManager) -> None:
    """Move stop to entry (breakeven) once a position profits by 1×stop-distance.

    Fetches Alpaca positions every 5 minutes. For each position whose
    unrealized P&L >= 1×ATR_stop_distance, cancels the bracket's stop leg
    and submits a new standalone stop order at the entry price.
    Protects winning trades from turning into losers without interfering
    with positions that haven't triggered the 1R profit threshold yet.
    """
    # Track which positions have already been locked to avoid repeated patches
    _locked: dict[str, float] = {}  # symbol → lock_price

    while True:
        await asyncio.sleep(300)  # check every 5 minutes
        if not _is_market_hours():
            continue
        # Stay fully hands-off in manual mode — don't touch user-approved stops.
        if not _auto_execute_enabled():
            continue
        try:
            positions = await broker.get_positions()
            if not positions:
                continue

            open_orders = await broker.get_open_orders()
            stop_orders: dict[str, list[dict]] = {}
            for o in open_orders:
                sym = o.get("symbol", "").upper()
                if stop_orders.get(sym) is None:
                    stop_orders[sym] = []
                stop_orders[sym].append(o)

            for pos in positions:
                sym  = pos.get("symbol", "").upper()
                side = pos.get("side", "long").lower()
                qty  = int(abs(float(pos.get("qty", 0))))
                if qty <= 0:
                    continue

                # Skip if we've already locked this position at this price
                already_locked = _locked.get(sym)

                # Compute stop distance for the CURRENT ATR setting
                bars = await broker.get_bars(sym, timeframe="5Min", limit=20)
                if bars is None or bars.empty:
                    continue
                from agents.risk_agent import RiskAgent as _RA
                atr = _RA._atr(bars)
                stop_dist = atr * pm.risk.cfg.atr_stop_multiple
                if stop_dist <= 0:
                    continue

                unreal = float(pos.get("unrealized_pl", 0))
                cost   = float(pos.get("market_value", 0)) - unreal  # approximate cost basis
                entry  = cost / qty if qty > 0 else 0
                if entry <= 0:
                    continue

                # Lock condition: unrealized P&L ≥ 1× stop-distance per share
                pl_per_share = unreal / qty
                if (side == "long"  and pl_per_share < stop_dist):
                    continue
                if (side == "short" and pl_per_share < stop_dist):
                    continue

                lock_price = round(entry, 2)
                if already_locked and abs(already_locked - lock_price) < 0.01:
                    continue  # already locked at this price

                # Cancel the bracket's stop child order
                for o in stop_orders.get(sym, []):
                    otype = o.get("type", "")
                    if otype in ("stop", "stop_limit") and o.get("id"):
                        await broker.cancel_order(o["id"])
                        logger.info("breakeven lock: cancelled stop order %s for %s", o["id"], sym)

                # Submit a new stop at the entry price (breakeven)
                exit_side = "sell" if side == "long" else "buy"
                new_id = await broker.submit_stop(sym, qty, exit_side, lock_price)
                if new_id:
                    _locked[sym] = lock_price
                    logger.info(
                        "BREAKEVEN LOCK %s: %.2f PnL/share ≥ ATR_stop %.2f → stop@entry %.2f",
                        sym, pl_per_share, stop_dist, lock_price,
                    )

        except Exception:
            logger.exception("breakeven_lock_loop error")


async def main(tickers: Sequence[str]) -> None:
    global _ticker_lock, _EVAL_SEMAPHORE
    _ticker_lock = asyncio.Lock()
    _EVAL_SEMAPHORE = asyncio.Semaphore(10)

    settings = load_settings()
    execute = os.environ.get("EXECUTE_LIVE", "false").lower() == "true"

    if not execute:
        logger.warning("EXECUTE_LIVE!=true -> DRY RUN (analysis only, no orders sent)")

    broker = build_broker(settings, force_live=True)
    logger.info("Broker: %s", type(broker).__name__)

    universe: UniverseScanner | None = None
    active_tickers: list[str] = list(tickers)

    # UniverseScanner always uses Alpaca data feed for market scanning
    # regardless of execution broker (IBKR or Alpaca)
    if not active_tickers and settings.scanner.enabled:
        universe = UniverseScanner(settings.alpaca_key_id, settings.alpaca_secret)
        logger.info("No tickers provided -- running UniverseScanner...")
        async with broker:
            # A startup network blip (DNS down, WiFi waking up) must not kill
            # the bot: retry a few times, then fall back to a liquid default
            # watchlist — rescan_loop replaces it once the scanner recovers.
            for attempt in range(1, 4):
                try:
                    active_tickers = await universe.get_candidates(
                        top_n=settings.scanner.top_n,
                        min_price=settings.scanner.min_price,
                        max_price=settings.scanner.max_price,
                        min_volume=settings.scanner.min_volume,
                        min_change=settings.scanner.min_change_pct,
                    )
                except Exception:
                    logger.exception("Universe scan attempt %d errored", attempt)
                    active_tickers = []
                if active_tickers:
                    break
                if attempt < 3:
                    logger.warning(
                        "Universe scan attempt %d returned no candidates — retrying in 30s",
                        attempt,
                    )
                    await asyncio.sleep(30)
        if not active_tickers:
            active_tickers = FALLBACK_WATCHLIST.copy()
            logger.warning(
                "Universe scanner unavailable — starting with fallback watchlist %s "
                "(auto-refreshes every %d min)",
                active_tickers, RESCAN_INTERVAL_MIN,
            )
        else:
            # Merge core watchlist into scanner results (deduplicate, core first)
            core = [t.upper() for t in CORE_WATCHLIST if t.strip()]
            merged = core + [t for t in active_tickers if t.upper() not in {c.upper() for c in core}]
            active_tickers = merged[:30]  # cap at 30 total
            logger.info("Auto-selected %d tickers (core+scanner): %s", len(active_tickers), active_tickers)
    elif not active_tickers:
        logger.error("No tickers provided and SCANNER_ENABLED=false -- nothing to do")
        return

    pm = build_manager(settings, broker)

    async with broker:
        await refresh_market_context(pm, broker)
        await pm.refresh_protections()

        logger.info("Initial scan of %s", active_tickers)
        await asyncio.gather(
            *[evaluate_ticker(pm, broker, t, execute=execute)
              for t in list(active_tickers)],
            return_exceptions=True,
        )

        loops = [
            rescan_loop(
                pm, broker, active_tickers,
                execute=execute,
                interval_min=RESCAN_INTERVAL_MIN,
                universe=universe,
                scanner_cfg=settings.scanner if universe else None,
            ),
            strategy_refresh_loop(pm, interval_min=STRATEGY_REFRESH_MIN),
            eod_report_loop(settings),
        ]
        if universe is not None:
            loops.append(
                breakout_monitor_loop(
                    pm, broker, active_tickers,
                    execute=execute,
                    interval_min=BREAKOUT_INTERVAL_MIN,
                    universe=universe,
                    min_change_pct=BREAKOUT_MIN_CHANGE,
                )
            )
        if execute:
            loops.append(eod_flatten_loop(broker, settings))
            loops.append(breakeven_lock_loop(broker, pm))
        await asyncio.gather(*loops)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
