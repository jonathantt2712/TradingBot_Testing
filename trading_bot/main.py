"""Composition root + run loops.

Usage:
    python main.py                   # auto-scan market (no tickers needed)
    python main.py AAPL MSFT         # manual ticker override
    python live_runner.py            # live mode (recommended)
    python backtest_runner.py AAPL   # walk-forward backtest + dashboard
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Sequence

import bootstrap  # loads .env files on import — keep first
from bootstrap import build_broker, build_manager, refresh_market_context
from config.settings import load_settings
from core.enums import RunMode
from core.models import AnalysisContext
from data.chart_renderer import render_chart
from data.sector_scanner import SectorScanner
from data.dashboard_publisher import push_scan_results
from data.universe_scanner import UniverseScanner
from execution.base_broker import BaseBroker
from execution.portfolio_manager import PortfolioManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("desk")


async def evaluate_ticker(pm: PortfolioManager, broker: BaseBroker, ticker: str, *, execute: bool):
    bars = await broker.get_bars(ticker, timeframe="5Min", limit=200)
    account = await broker.get_account()
    chart = render_chart(ticker, bars)
    ctx = AnalysisContext(ticker=ticker, bars=bars, account=account, chart_image_path=chart)
    decision = await pm.run_once(ctx, execute=execute)
    logger.info("%s -> %s | composite=%.1f | %s",
                ticker, decision.decision.value, decision.composite_score,
                pm.summarise(decision.evaluations))
    if decision.is_actionable and decision.risk:
        r = decision.risk
        logger.info("  plan qty=%g entry=%.2f SL=%.2f TP=%.2f R/R=%.2f",
                    r.qty, r.entry, r.stop_loss, r.take_profit, r.risk_reward)
    return decision


async def main(tickers: Sequence[str]) -> None:
    settings = load_settings()

    tickers_list: list[str] = list(tickers)
    if not tickers_list:
        if settings.scanner.enabled:
            broker_tmp = build_broker(settings)
            universe = UniverseScanner(settings.alpaca_key_id, settings.alpaca_secret)
            logger.info("No tickers provided -- running UniverseScanner...")
            async with broker_tmp:
                tickers_list = await universe.get_candidates(
                    top_n=settings.scanner.top_n,
                    min_price=settings.scanner.min_price,
                    max_price=settings.scanner.max_price,
                    min_volume=settings.scanner.min_volume,
                    min_change=settings.scanner.min_change_pct,
                )
            if not tickers_list:
                logger.error("Universe scanner returned no candidates -- exiting")
                return
        else:
            logger.error("No tickers provided and SCANNER_ENABLED=false -- nothing to do")
            return

    logger.info("run_mode=%s tickers=%s", settings.run_mode.value, tickers_list)

    broker = build_broker(settings)
    pm = build_manager(settings, broker)

    execute = (
        settings.run_mode is RunMode.BACKTEST
        or os.environ.get("EXECUTE_LIVE", "false").lower() == "true"
    )
    if settings.run_mode is RunMode.LIVE and not execute:
        logger.warning("LIVE mode but EXECUTE_LIVE!=true -> DRY RUN")

    async with broker:
        # 1. Market regime + SPY bars for relative strength
        regime = await refresh_market_context(pm, broker)

        # 2. Sector scan
        scanner = SectorScanner(broker)
        scan    = await scanner.scan(tickers_list)
        hot     = set(scan.hot_tickers(top_n_sectors=2))
        if hot:
            logger.info("Hot tickers (top 2 sectors): %s | sectors: %s",
                        sorted(hot), scan.sector_summary())

        # 3. Evaluate tickers concurrently
        results = await asyncio.gather(
            *[evaluate_ticker(pm, broker, t, execute=execute) for t in tickers_list],
            return_exceptions=True,
        )
        decisions = []
        for ticker, result in zip(tickers_list, results):
            if isinstance(result, Exception):
                logger.exception("evaluation failed for %s: %s", ticker, result)
            else:
                decisions.append(result)
                if ticker.upper() not in hot:
                    logger.info("  %s is in a cold sector -- signal noted but deprioritised", ticker)

        # 4. Push signals to dashboard API
        try:
            await push_scan_results(
                decisions=decisions,
                regime=regime,
                scan_report=scan,
            )
        except Exception as e:
            logger.debug("Dashboard push skipped: %s", e)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
