"""Shared composition helpers for all entry points (main, live_runner, ...).

Keeps env loading, broker selection, news wiring, and PortfolioManager
construction in one place so the one-shot and live runners cannot drift apart.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def load_env() -> None:
    """Load .env files from the workspace root and dashboard (first hit wins)."""
    root = Path(__file__).parent.parent
    for f in [root / ".env", root / ".env.local", root / "trading-dashboard" / ".env.local"]:
        if f.exists():
            load_dotenv(f, override=False)


load_env()  # must run before config.settings is imported by callers

from config.settings import Settings  # noqa: E402
from core.enums import RunMode  # noqa: E402
from agents.decision_agent import DecisionAgent  # noqa: E402
from agents.fundamental_agent import FundamentalAgent  # noqa: E402
from agents.macro_agent import MacroSignalAgent  # noqa: E402
from agents.liquid_agent import LiquidAgent  # noqa: E402
from agents.regime_agent import detect_regime  # noqa: E402
from agents.risk_agent import RiskAgent  # noqa: E402
from agents.insider_agent import InsiderAgent  # noqa: E402
from agents.social_agent import SocialSentimentAgent  # noqa: E402
from agents.squeeze_agent import SqueezeAgent  # noqa: E402
from agents.technical_agent import TechnicalAgent  # noqa: E402
from agents.vision_agent import VisionAgent  # noqa: E402
from data.ai4trade_client import AI4TradeClient  # noqa: E402
from data.market_intel_source import CombinedNewsSource, MarketIntelNewsSource  # noqa: E402
from data.news_sources import AlpacaNewsSource, NewsSource, PoliStockSource  # noqa: E402
from execution.alpaca_broker import AlpacaBroker  # noqa: E402
from execution.base_broker import BaseBroker  # noqa: E402
from execution.ibkr_broker import IBKRBroker  # noqa: E402
from execution.liquid_broker import LiquidBroker  # noqa: E402
from execution.portfolio_manager import PortfolioManager  # noqa: E402
from execution.signal_publisher import SignalPublisher  # noqa: E402


def build_broker(settings: Settings, *, force_live: bool = False) -> BaseBroker:
    """Select the execution broker.

    ``force_live=True`` (live_runner) honours BROKER/USE_LIQUID_BROKER even when
    RUN_MODE is left at its backtest default.
    """
    live = force_live or settings.run_mode is RunMode.LIVE
    if live and settings.use_liquid_broker:
        return LiquidBroker(settings.liquid_api_key)
    if live and settings.broker == "ibkr":
        return IBKRBroker(settings.ibkr_host, settings.ibkr_port, settings.ibkr_client_id)
    return AlpacaBroker(
        settings.alpaca_key_id, settings.alpaca_secret,
        paper=settings.alpaca_paper, feed=settings.alpaca_data_feed,
    )


def build_news(settings: Settings, ai4: AI4TradeClient | None = None) -> NewsSource:
    alpaca_news: NewsSource = (
        AlpacaNewsSource(settings.alpaca_key_id, settings.alpaca_secret)
        if settings.alpaca_key_id
        else PoliStockSource(settings.news_base_url, settings.news_api_key)
    )
    if ai4 is None:
        return alpaca_news
    return CombinedNewsSource(alpaca_news, MarketIntelNewsSource(ai4))


def build_manager(
    settings: Settings,
    broker: BaseBroker | None,
    ai4: AI4TradeClient | None = None,
    *,
    publisher: SignalPublisher | None = None,
    include_live_only_agents: bool = True,
    include_vision: bool = True,
    include_decision_agent: bool = True,
    include_insider: bool = True,
    include_squeeze: bool = True,
) -> PortfolioManager:
    """Single composition point for every runner, including backtests.

    ``include_live_only_agents=False`` (backtests) drops the social and liquid
    agents: their data sources report CURRENT platform state, which would leak
    look-ahead noise into historical evaluations.

    ``include_vision=False`` (backtests) skips VisionAgent's LLM chart analysis.
    Historical backtests evaluate hundreds of windows; each LLM call costs money
    and time, making it impractical to include vision in offline simulations.

    ``include_decision_agent=False`` (backtests) skips DecisionAgent's LLM call.
    A 30-day backtest generates ~500 evaluation windows, making per-window LLM
    calls prohibitively expensive.
    """
    news = build_news(settings, ai4)
    live_extras = include_live_only_agents
    squeeze_agent = SqueezeAgent(weight=settings.weights.squeeze) if include_squeeze else None
    macro_agent   = MacroSignalAgent(weight=settings.weights.macro)
    return PortfolioManager(
        settings=settings,
        broker=broker,
        fundamental=FundamentalAgent(news, weight=settings.weights.fundamental,
                                     anthropic_api_key=settings.anthropic_api_key,
                                     gemini_api_key=settings.gemini_api_key,
                                     model=settings.llm_model),
        vision=VisionAgent(weight=settings.weights.vision,
                           anthropic_api_key=settings.anthropic_api_key,
                           gemini_api_key=settings.gemini_api_key,
                           model=settings.llm_model) if include_vision else None,
        technical=TechnicalAgent(weight=settings.weights.technical),
        risk=RiskAgent(settings.risk),
        liquid=LiquidAgent(weight=settings.weights.liquid)
            if live_extras and settings.weights.liquid > 0 else None,
        social=SocialSentimentAgent(ai4, weight=settings.weights.social)
            if live_extras and ai4 is not None and settings.weights.social > 0 else None,
        insider=InsiderAgent(weight=settings.weights.insider)
            if live_extras and include_insider and settings.weights.insider > 0 else None,
        squeeze=squeeze_agent,
        macro=macro_agent,
        publisher=publisher,
        decision_agent=DecisionAgent(
            anthropic_api_key=settings.anthropic_api_key,
            gemini_api_key=settings.gemini_api_key,
            model=settings.llm_model,
        ) if include_decision_agent else None,
    )


async def refresh_market_context(pm: PortfolioManager, broker: BaseBroker):
    """Detect the macro regime and inject SPY bars into the TechnicalAgent.

    Called once per scan cycle by every runner so regime gating and the
    relative-strength signal are active in all modes, not just main.py.
    Returns the RegimeSnapshot (or None if detection failed).
    """
    regime = None
    try:
        regime = await detect_regime(broker)
        pm.set_regime(regime)
    except Exception:
        logger.exception("regime detection failed — keeping previous regime")
    try:
        spy_bars = await broker.get_bars("SPY", timeframe="5Min", limit=120)
        if spy_bars is not None and not spy_bars.empty:
            pm.technical.spy_bars = spy_bars
    except Exception:
        logger.exception("SPY bars fetch failed — relative strength unavailable")
    return regime


async def eod_flatten_loop(broker: BaseBroker, settings: Settings) -> None:
    """Close all positions shortly before the 16:00 ET close (day-trade-only bot).

    Checks once a minute; fires once per trading day in the window
    [close - eod_flatten_min_before, close).
    """
    if not settings.eod_flatten:
        logger.info("EOD flatten disabled (EOD_FLATTEN=false)")
        return

    flattened_on = None
    while True:
        now = datetime.now(_ET)
        is_weekday = now.weekday() < 5
        close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        window_start = close - timedelta(minutes=settings.eod_flatten_min_before)

        if is_weekday and window_start <= now < close and flattened_on != now.date():
            logger.info("EOD flatten window reached — closing all positions")
            ok = await broker.close_all_positions()
            if ok:
                flattened_on = now.date()
            else:
                logger.error("EOD flatten failed — will retry next minute")
        await asyncio.sleep(60)
