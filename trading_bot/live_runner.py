"""Heartbeat-driven live runner.

Replaces the one-shot polling loop in main.py for LIVE mode.
Instead of evaluating tickers on a fixed interval, the bot:

  1. Auto-scans the market for candidates (most-active + gainers/losers)
     OR accepts an explicit ticker list from the CLI.
  2. Detects the macro regime + injects SPY bars (refreshed every rescan).
  3. Runs an initial evaluation of all candidates on startup.
  4. Subscribes to the AI4Trade heartbeat loop.
  5. Re-evaluates any ticker mentioned in incoming platform tasks/messages.
  6. Re-scans the market universe every RESCAN_INTERVAL_MIN minutes and
     refreshes the active ticker list (shared with the heartbeat handler).
  7. Flattens all positions shortly before the 16:00 ET close (EOD_FLATTEN).

Usage:
    python live_runner.py              # auto-scan (no args needed)
    python live_runner.py AAPL NVDA   # manual override
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Sequence

import bootstrap  # loads .env files on import — keep first
from bootstrap import build_broker, build_manager, eod_flatten_loop, refresh_market_context
from config.settings import load_settings
from core.models import AnalysisContext
from data.chart_renderer import render_chart
from data.ai4trade_client import AI4TradeClient
from data.universe_scanner import UniverseScanner
from execution.base_broker import BaseBroker
from execution.portfolio_manager import PortfolioManager
from execution.signal_publisher import SignalPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("live")

RESCAN_INTERVAL_MIN   = int(os.environ.get("RESCAN_INTERVAL_MIN",   "30"))
BREAKOUT_INTERVAL_MIN = int(os.environ.get("BREAKOUT_INTERVAL_MIN", "5"))
BREAKOUT_MIN_CHANGE   = float(os.environ.get("BREAKOUT_MIN_CHANGE_PCT", "3.0"))

# Used when the universe scanner is unreachable at startup (network blip):
# deep-liquidity names so the bot stays alive until the scanner recovers.
FALLBACK_WATCHLIST = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA"]


async def evaluate_ticker(
    pm: PortfolioManager,
    broker: BaseBroker,
    ticker: str,
    *,
    execute: bool,
    publisher: SignalPublisher | None,
) -> None:
    try:
        bars = await broker.get_bars(ticker, timeframe="5Min", limit=200)
        account = await broker.get_account()
        chart = render_chart(ticker, bars)
        ctx = AnalysisContext(ticker=ticker, bars=bars, account=account, chart_image_path=chart)
        decision = await pm.run_once(ctx, execute=execute)
        logger.info(
            "%s -> %s | composite=%.1f | %s",
            ticker, decision.decision.value, decision.composite_score,
            pm.summarise(decision.evaluations),
        )
        if publisher:
            await publisher.publish(decision)
    except Exception:
        logger.exception("evaluation failed for %s", ticker)


async def handle_heartbeat(
    messages: list,
    tasks: list,
    pm: PortfolioManager,
    broker: BaseBroker,
    active_tickers: list[str],
    *,
    execute: bool,
    publisher: SignalPublisher | None,
) -> None:
    """React to heartbeat events -- re-evaluate tickers mentioned in messages.

    ``active_tickers`` is the live, shared list mutated in place by
    rescan_loop, so newly scanned symbols are heartbeat-eligible too.
    """
    triggered: set[str] = set()

    for msg in messages:
        logger.info(
            "AI4Trade [%s]: %s",
            msg.get("type", "?"),
            msg.get("content", "")[:100],
        )
        data = msg.get("data") or {}
        symbol = data.get("symbol") or data.get("ticker")
        if symbol and symbol.upper() in {t.upper() for t in active_tickers}:
            triggered.add(symbol.upper())

    for task in tasks:
        logger.info("AI4Trade task: %s", task.get("type"))
        inp = task.get("input_data") or {}
        symbol = inp.get("symbol") or inp.get("ticker")
        if symbol:
            triggered.add(symbol.upper())

    if triggered:
        logger.info("Heartbeat triggered re-eval for: %s", triggered)
        await asyncio.gather(
            *[evaluate_ticker(pm, broker, t, execute=execute, publisher=publisher) for t in triggered],
            return_exceptions=True,
        )


async def breakout_monitor_loop(
    pm: PortfolioManager,
    broker: BaseBroker,
    active_tickers: list[str],
    *,
    execute: bool,
    publisher: SignalPublisher | None,
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
            for sym in breakouts:
                if sym not in active_tickers:
                    active_tickers.append(sym)
            logger.info("BREAKOUT ALERT: %s — evaluating immediately", breakouts)
            await asyncio.gather(
                *[evaluate_ticker(pm, broker, t, execute=execute, publisher=publisher)
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
    publisher: SignalPublisher | None,
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

        logger.info("Scheduled rescan of %s", active_tickers)
        await asyncio.gather(
            *[evaluate_ticker(pm, broker, t, execute=execute, publisher=publisher)
              for t in list(active_tickers)],
            return_exceptions=True,
        )


async def main(tickers: Sequence[str]) -> None:
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
            logger.info("Auto-selected %d tickers: %s", len(active_tickers), active_tickers)
    elif not active_tickers:
        logger.error("No tickers provided and SCANNER_ENABLED=false -- nothing to do")
        return

    ai4 = AI4TradeClient()
    await ai4.__aenter__()

    pm = build_manager(settings, broker, ai4)
    publisher = SignalPublisher(ai4, publish_pass=True) if ai4.token else None

    async with broker:
        await refresh_market_context(pm, broker)

        logger.info("Initial scan of %s", active_tickers)
        await asyncio.gather(
            *[evaluate_ticker(pm, broker, t, execute=execute, publisher=publisher)
              for t in active_tickers],
            return_exceptions=True,
        )

        async def hb_callback(messages, tasks):
            await handle_heartbeat(messages, tasks, pm, broker, active_tickers,
                                   execute=execute, publisher=publisher)

        loops = [
            ai4.heartbeat_loop(hb_callback),
            rescan_loop(
                pm, broker, active_tickers,
                execute=execute,
                publisher=publisher,
                interval_min=RESCAN_INTERVAL_MIN,
                universe=universe,
                scanner_cfg=settings.scanner if universe else None,
            ),
        ]
        if universe is not None:
            loops.append(
                breakout_monitor_loop(
                    pm, broker, active_tickers,
                    execute=execute,
                    publisher=publisher,
                    interval_min=BREAKOUT_INTERVAL_MIN,
                    universe=universe,
                    min_change_pct=BREAKOUT_MIN_CHANGE,
                )
            )
        if execute:
            loops.append(eod_flatten_loop(broker, settings))
        await asyncio.gather(*loops)

    await ai4.__aexit__(None, None, None)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
