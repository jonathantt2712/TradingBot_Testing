"""Heartbeat-driven live runner.

Replaces the one-shot polling loop in main.py for LIVE mode.
Instead of evaluating tickers on a fixed interval, the bot:

  1. Auto-scans the market for candidates (most-active + gainers/losers)
     OR accepts an explicit ticker list from the CLI.
  2. Runs an initial evaluation of all candidates on startup.
  3. Subscribes to the AI4Trade heartbeat loop.
  4. Re-evaluates any ticker mentioned in incoming platform tasks/messages.
  5. Re-scans the market universe every RESCAN_INTERVAL_MIN minutes and
     refreshes the active ticker list.

Usage:
    python live_runner.py              # auto-scan (no args needed)
    python live_runner.py AAPL NVDA   # manual override
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Sequence

from pathlib import Path
from dotenv import load_dotenv
_root = Path(__file__).parent.parent
for _f in [_root / ".env", _root / ".env.local", _root / "trading-dashboard" / ".env.local"]:
    if _f.exists(): load_dotenv(_f, override=False)

from config.settings import load_settings
from core.models import AnalysisContext
from data.chart_renderer import render_chart
from data.ai4trade_client import AI4TradeClient
from data.market_intel_source import CombinedNewsSource, MarketIntelNewsSource
from data.news_sources import AlpacaNewsSource, PoliStockSource
from data.universe_scanner import UniverseScanner
from agents.fundamental_agent import FundamentalAgent
from agents.liquid_agent import LiquidAgent
from agents.risk_agent import RiskAgent
from agents.social_agent import SocialSentimentAgent
from agents.technical_agent import TechnicalAgent
from agents.vision_agent import VisionAgent
from execution.alpaca_broker import AlpacaBroker
from execution.base_broker import BaseBroker
from execution.ibkr_broker import IBKRBroker
from execution.portfolio_manager import PortfolioManager
from execution.signal_publisher import SignalPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("live")

RESCAN_INTERVAL_MIN = int(os.environ.get("RESCAN_INTERVAL_MIN", "30"))


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
    default_tickers: list[str],
    *,
    execute: bool,
    publisher: SignalPublisher | None,
) -> None:
    """React to heartbeat events -- re-evaluate tickers mentioned in messages."""
    triggered: set[str] = set()

    for msg in messages:
        logger.info(
            "AI4Trade [%s]: %s",
            msg.get("type", "?"),
            msg.get("content", "")[:100],
        )
        data = msg.get("data") or {}
        symbol = data.get("symbol") or data.get("ticker")
        if symbol and symbol.upper() in {t.upper() for t in default_tickers}:
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


async def rescan_loop(
    pm: PortfolioManager,
    broker: BaseBroker,
    tickers: list[str],
    *,
    execute: bool,
    publisher: SignalPublisher | None,
    interval_min: int,
    universe: UniverseScanner | None = None,
    scanner_cfg=None,
) -> None:
    """Re-evaluate tickers every interval_min minutes."""
    active = tickers
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
                    added   = set(fresh) - set(active)
                    removed = set(active) - set(fresh)
                    active  = fresh
                    if added or removed:
                        logger.info(
                            "Universe refreshed: +%s -%s -> active=%s",
                            sorted(added), sorted(removed), active
                        )
            except Exception:
                logger.exception("Universe refresh failed -- keeping previous list")

        logger.info("Scheduled rescan of %s", active)
        await asyncio.gather(
            *[evaluate_ticker(pm, broker, t, execute=execute, publisher=publisher) for t in active],
            return_exceptions=True,
        )


async def main(tickers: Sequence[str]) -> None:
    settings = load_settings()
    execute = os.environ.get("EXECUTE_LIVE", "false").lower() == "true"

    if not execute:
        logger.warning("EXECUTE_LIVE!=true -> DRY RUN (analysis only, no orders sent)")

    broker: BaseBroker = AlpacaBroker(
        settings.alpaca_key_id, settings.alpaca_secret, paper=True
    )

    universe: UniverseScanner | None = None
    tickers_list: list[str] = list(tickers)

    if not tickers_list and settings.scanner.enabled:
        universe = UniverseScanner(settings.alpaca_key_id, settings.alpaca_secret)
        logger.info("No tickers provided -- running UniverseScanner...")
        async with broker:
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
        logger.info("Auto-selected %d tickers: %s", len(tickers_list), tickers_list)
    elif not tickers_list:
        logger.error("No tickers provided and SCANNER_ENABLED=false -- nothing to do")
        return

    ai4 = AI4TradeClient()
    await ai4.__aenter__()

    alpaca_news = AlpacaNewsSource(settings.alpaca_key_id, settings.alpaca_secret) \
        if settings.alpaca_key_id else PoliStockSource(settings.news_base_url, settings.news_api_key)
    intel_news = MarketIntelNewsSource(ai4)
    news = CombinedNewsSource(alpaca_news, intel_news)

    social = SocialSentimentAgent(ai4, weight=settings.weights.social)
    pm = PortfolioManager(
        settings=settings,
        broker=broker,
        fundamental=FundamentalAgent(news, weight=settings.weights.fundamental,
                                     anthropic_api_key=settings.anthropic_api_key,
                                     model=settings.llm_model),
        vision=VisionAgent(weight=settings.weights.vision,
                           anthropic_api_key=settings.anthropic_api_key,
                           model=settings.llm_model),
        technical=TechnicalAgent(weight=settings.weights.technical),
        risk=RiskAgent(settings.risk),
        liquid=LiquidAgent(weight=settings.weights.liquid) if settings.weights.liquid > 0 else None,
        social=social,
    )

    publisher = SignalPublisher(ai4, publish_pass=True) if ai4.token else None

    async with broker:
        logger.info("Initial scan of %s", tickers_list)
        await asyncio.gather(
            *[evaluate_ticker(pm, broker, t, execute=execute, publisher=publisher) for t in tickers_list],
            return_exceptions=True,
        )

        async def hb_callback(messages, tasks):
            await handle_heartbeat(messages, tasks, pm, broker, tickers_list,
                                   execute=execute, publisher=publisher)

        await asyncio.gather(
            ai4.heartbeat_loop(hb_callback),
            rescan_loop(
                pm, broker, tickers_list,
                execute=execute,
                publisher=publisher,
                interval_min=RESCAN_INTERVAL_MIN,
                universe=universe,
                scanner_cfg=settings.scanner if universe else None,
            ),
        )

    await ai4.__aexit__(None, None, None)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
