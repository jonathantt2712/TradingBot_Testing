"""Shared composition helpers for all entry points (main, live_runner, ...).

Keeps env loading, broker selection, news wiring, and PortfolioManager
construction in one place so the one-shot and live runners cannot drift apart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
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
from core import health  # noqa: E402
from core.enums import RunMode  # noqa: E402
from agents.decision_agent import DecisionAgent  # noqa: E402
from agents.fundamental_agent import FundamentalAgent  # noqa: E402
from agents.macro_agent import MacroSignalAgent  # noqa: E402
from agents.liquid_agent import LiquidAgent  # noqa: E402
from agents.regime_agent import detect_regime  # noqa: E402
from agents.risk_agent import RiskAgent  # noqa: E402
from agents.insider_agent import InsiderAgent  # noqa: E402
from agents.squeeze_agent import SqueezeAgent  # noqa: E402
from agents.technical_agent import TechnicalAgent  # noqa: E402
from agents.vision_agent import VisionAgent  # noqa: E402
from agents.report_agent import EODReportAgent  # noqa: E402
from data.correlation_graph import CorrelationGraph  # noqa: E402
from data.news_sources import AlpacaNewsSource, NewsSource, PoliStockSource  # noqa: E402
from execution.alpaca_broker import AlpacaBroker  # noqa: E402
from execution.base_broker import BaseBroker  # noqa: E402
from execution.ibkr_broker import IBKRBroker  # noqa: E402
from execution.liquid_broker import LiquidBroker  # noqa: E402
from execution.portfolio_manager import PortfolioManager  # noqa: E402


# Runtime broker selection written by the dashboard (/api/broker-mode). Read on
# every session (re)start so the toggle takes effect without editing .env.
from core.paths import data_dir as _data_dir  # noqa: E402
_BROKER_MODE_FILE = _data_dir() / "broker_mode.json"


def active_broker(settings: Settings) -> str:
    """The selected execution broker: 'alpaca' or 'ibkr'.

    The dashboard toggle (broker_mode.json) wins when present and valid; otherwise
    the BROKER env default applies. Liquid is a separate flag (USE_LIQUID_BROKER)
    and is not part of this toggle.
    """
    try:
        if _BROKER_MODE_FILE.exists():
            data = json.loads(_BROKER_MODE_FILE.read_text(encoding="utf-8"))
            choice = str(data.get("broker", "")).lower()
            if choice in ("alpaca", "ibkr"):
                return choice
    except Exception:
        logger.debug("broker_mode.json unreadable — falling back to BROKER env", exc_info=True)
    return settings.broker


def build_broker(settings: Settings, *, force_live: bool = False) -> BaseBroker:
    """Select the execution broker.

    ``force_live=True`` (live_runner) honours the broker toggle / BROKER /
    USE_LIQUID_BROKER even when RUN_MODE is left at its backtest default.
    """
    live = force_live or settings.run_mode is RunMode.LIVE
    if live and settings.use_liquid_broker:
        return LiquidBroker(settings.liquid_api_key)
    if live and active_broker(settings) == "ibkr":
        return IBKRBroker(settings.ibkr_host, settings.ibkr_port, settings.ibkr_client_id)
    return AlpacaBroker(
        settings.alpaca_key_id, settings.alpaca_secret,
        paper=settings.alpaca_paper, feed=settings.alpaca_data_feed,
    )


def build_news(settings: Settings) -> NewsSource:
    return (
        AlpacaNewsSource(settings.alpaca_key_id, settings.alpaca_secret)
        if settings.alpaca_key_id
        else PoliStockSource(settings.news_base_url, settings.news_api_key)
    )


def build_manager(
    settings: Settings,
    broker: BaseBroker | None,
    *,
    include_live_only_agents: bool = True,
    include_vision: bool | None = None,
    include_decision_agent: bool | None = None,
    include_insider: bool = True,
    include_squeeze: bool = True,
) -> PortfolioManager:
    """Single composition point for every runner, including backtests.

    ``include_live_only_agents=False`` (backtests) drops the social and liquid
    agents: their data sources report CURRENT platform state, which would leak
    look-ahead noise into historical evaluations.

    ``include_vision`` controls whether VisionAgent is allowed to call the LLM
    (``llm_enabled``) — the agent itself is ALWAYS constructed and contributes
    a real signal either way: with the LLM, a rendered-chart read; without it,
    a deterministic swing-high/low structure read of the same OHLCV bars every
    other agent uses (see VisionAgent._structure_evaluation). No cost trade-off
    to skipping it anymore, so nothing ever needs to pass with a placeholder
    neutral for lack of an LLM key/call.

    ``include_decision_agent`` still controls whether DecisionAgent is
    constructed at all — with it off, PortfolioManager falls back to the
    weighted composite/threshold path (_composite/_direction).

    Both default to ``settings.use_llm_agents`` (env ``USE_LLM_AGENTS``,
    default off) when the caller doesn't pass an explicit value. Backtests/
    optimizer pass an explicit False regardless of the setting — DecisionAgent
    calls per evaluation window are prohibitively slow/expensive at that
    volume, and with it off, live trading runs the SAME deterministic code
    path backtests and the optimizer validate against.
    """
    if include_vision is None:
        include_vision = settings.use_llm_agents
    if include_decision_agent is None:
        include_decision_agent = settings.use_llm_agents

    news = build_news(settings)
    live_extras = include_live_only_agents
    squeeze_agent = SqueezeAgent(weight=settings.weights.squeeze) if include_squeeze else None
    macro_agent   = MacroSignalAgent(weight=settings.weights.macro)
    return PortfolioManager(
        settings=settings,
        broker=broker,
        fundamental=FundamentalAgent(news, weight=settings.weights.fundamental,
                                     gemini_api_key=settings.gemini_api_key,
                                     llm_enabled=settings.use_llm_agents),
        vision=VisionAgent(weight=settings.weights.vision,
                           gemini_api_key=settings.gemini_api_key,
                           cache_ttl_min=settings.vision_cache_ttl_min,
                           llm_enabled=include_vision),
        technical=TechnicalAgent(weight=settings.weights.technical),
        risk=RiskAgent(settings.risk),
        liquid=LiquidAgent(weight=settings.weights.liquid)
            if live_extras and settings.weights.liquid > 0 else None,
        insider=InsiderAgent(weight=settings.weights.insider)
            if live_extras and include_insider and settings.weights.insider > 0 else None,
        squeeze=squeeze_agent,
        macro=macro_agent,
        decision_agent=DecisionAgent(
            gemini_api_key=settings.gemini_api_key,
        ) if include_decision_agent else None,
        # Live runners must not forget the daily kill-switch across restarts.
        # Backtests never call _check_daily_loss, so this is inert for them.
        persist_state=True,
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
    if os.environ.get("ALLOW_OVERNIGHT", "").lower() in ("1", "true", "yes"):
        # The EOD position review (api_server) may deliberately hold positions
        # overnight; flattening here would silently override those decisions.
        logger.warning("EOD flatten disabled: ALLOW_OVERNIGHT=true — the EOD "
                       "position review decides what stays open")
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


async def correlation_refresh_loop(
    pm: PortfolioManager,
    broker: BaseBroker,
    active_tickers: list[str],
    *,
    interval_min: int,
) -> None:
    """Rebuild the data-derived correlation graph for the concentration cap.

    Periodically fetches daily bars for the active universe plus any open
    positions and injects a fresh :class:`CorrelationGraph` into the
    PortfolioManager. Heavy work stays here, off the per-entry hot path, which
    only reads the cached graph. No-ops when the cap is disabled.
    """
    cap = pm.settings.risk.max_correlated_positions
    if cap <= 0:
        logger.info("Correlation refresh disabled (MAX_CORRELATED_POSITIONS=0)")
        return

    threshold = pm.settings.risk.correlation_threshold
    while True:
        try:
            symbols = {t.upper() for t in active_tickers}
            try:
                positions = await broker.get_positions()
                symbols |= {str(p.get("symbol", "")).upper()
                            for p in positions if p.get("symbol")}
            except Exception:
                logger.debug("correlation refresh: positions unavailable", exc_info=True)

            bars_by: dict = {}
            for sym in symbols:
                try:
                    bars = await broker.get_bars(sym, timeframe="1Day", limit=60)
                    if bars is not None and not bars.empty:
                        bars_by[sym] = bars
                except Exception:
                    continue

            if len(bars_by) >= 2:
                graph = CorrelationGraph.build_from_bars(bars_by, threshold=threshold)
                pm.set_correlation_graph(graph)
                logger.info("Correlation graph refreshed over %d symbols", len(bars_by))
        except Exception:
            logger.exception("correlation refresh failed — keeping previous graph")
        await asyncio.sleep(interval_min * 60)


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """True if a TCP connection to host:port opens within ``timeout`` seconds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def preflight_checks(settings: Settings) -> None:
    """At startup, tell the operator what the bot needs that isn't configured.

    Only reports genuinely missing essentials — these surface in the log (via
    health_alert_loop), on the dashboard, and in the EOD report. Reported through
    the health board so they dedupe with any runtime failures of the same thing.
    """
    active = active_broker(settings)
    # Alpaca keys are needed for execution when Alpaca is selected, and ALWAYS for
    # the universe scanner / news feed — so warn whenever they're missing.
    if not (settings.alpaca_key_id and settings.alpaca_secret):
        if active == "alpaca" and not settings.use_liquid_broker:
            health.report_issue(
                "config:alpaca_keys",
                "Alpaca API keys are not set.",
                remediation="Set ALPACA_API_KEY_ID and ALPACA_API_SECRET — without them the "
                            "bot can't fetch market data, size positions, or trade.",
            )
        else:
            health.report_issue(
                "config:alpaca_keys",
                "Alpaca API keys are not set (needed for the market scanner / news feed).",
                remediation="Set ALPACA_API_KEY_ID and ALPACA_API_SECRET so the universe "
                            "scanner can find candidates; otherwise the bot trades only the "
                            "fallback watchlist.",
                severity="warning",
            )
    if active == "ibkr" and not settings.use_liquid_broker:
        try:
            import ib_insync  # noqa: F401
        except Exception:
            health.report_issue(
                "config:ibkr_lib",
                "BROKER=ibkr but the ib_insync library isn't installed.",
                remediation="Run `pip install -r requirements.txt` in trading_bot/ to "
                            "install ib-insync.",
            )
        else:
            if not _tcp_reachable(settings.ibkr_host, settings.ibkr_port):
                health.report_issue(
                    "config:ibkr_conn",
                    f"Can't reach IBKR TWS/Gateway at {settings.ibkr_host}:{settings.ibkr_port}.",
                    remediation="Start TWS or IB Gateway and enable the API: Global Config → "
                                "API → Settings → check 'Enable ActiveX and Socket Clients', "
                                "uncheck 'Read-Only API', and confirm the socket port "
                                "(7497 = TWS paper, 4002 = IB Gateway paper) matches IBKR_PORT.",
                )
    # An LLM key is only worth reporting when the operator opted INTO LLM
    # analysis: the default pipeline is pure logic, so a missing key is not a
    # missing essential — it's the normal, zero-token configuration.
    if (settings.use_llm_agents
            and not settings.gemini_api_key
            and not os.environ.get("ANTHROPIC_API_KEY")):
        health.report_issue(
            "config:llm_key",
            "USE_LLM_AGENTS=true but no LLM API key is set (GEMINI_API_KEY / ANTHROPIC_API_KEY).",
            remediation="Set one, or leave USE_LLM_AGENTS off to run the "
                        "deterministic (no-token) pipeline.",
            severity="warning",
        )
    if not settings.telegram_bot_token:
        logger.info("Telegram not configured — trade alerts will only appear in the log.")


async def heartbeat_loop(*, execute: bool, broker_name: str,
                         active_tickers: list[str], interval_s: int = 60) -> None:
    """Prove the live runner is alive: write a heartbeat file once a minute.

    api_server (same machine in the standard setup) surfaces a health issue
    when the heartbeat goes stale during market hours — a crashed live_runner
    otherwise fails SILENT: no trades, no errors, nothing on the dashboard.
    """
    hb_file = _data_dir() / "live_heartbeat.json"
    while True:
        try:
            hb_file.write_text(json.dumps({
                "ts": datetime.now(ZoneInfo("UTC")).replace(tzinfo=None).isoformat(),
                "execute": execute,
                "broker": broker_name,
                "tickers": len(active_tickers),
            }), encoding="utf-8")
        except Exception:
            logger.debug("heartbeat write failed", exc_info=True)
        await asyncio.sleep(interval_s)


async def health_alert_loop(*, interval_min: int = 10) -> None:
    """Log newly-reported issues so the operator is told promptly.

    Runs the first check immediately (catches startup preflight issues), then
    every interval_min. Telegram is reserved for actual buys and sells, so
    issues surface here and on the dashboard's health board instead.
    """
    while True:
        try:
            for issue in health.take_unsent():
                logger.warning("NEEDS ATTENTION: %s", issue.as_line())
        except Exception:
            logger.exception("health alert logging failed")
        await asyncio.sleep(interval_min * 60)


async def eod_report_loop(settings: Settings) -> None:
    """Publish an end-of-day desk note once per trading day near the close.

    Fires in the window [close - eod_report_min_before, close); checks once a
    minute. Reads only the bot's own recorded activity (audit log / trade
    history / memory), so it runs in both live and dry-run modes. The note goes
    to the log — Telegram carries buys and sells only.
    """
    if not settings.eod_report:
        logger.info("EOD report disabled (EOD_REPORT=false)")
        return

    agent = EODReportAgent(gemini_api_key=settings.gemini_api_key,
                           llm_enabled=settings.use_llm_agents)
    reported_on = None
    while True:
        now = datetime.now(_ET)
        is_weekday = now.weekday() < 5
        close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        window_start = close - timedelta(minutes=settings.eod_report_min_before)

        if is_weekday and window_start <= now < close and reported_on != now.date():
            try:
                report = await agent.generate()
                logger.info("EOD REPORT:\n%s", report)
                reported_on = now.date()
            except Exception:
                logger.exception("EOD report failed — will retry next minute")
        await asyncio.sleep(60)
