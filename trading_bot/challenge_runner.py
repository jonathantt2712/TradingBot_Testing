"""Challenge Competition Runner.

Registers the bot in active AI4Trade trading challenges and auto-submits
its trade decisions for objective benchmarking against other AI agents.

Challenge track auto-selection:
  - Tickers in _CRYPTO → join crypto challenges
  - Everything else → join us-stock challenges

Usage:
    python challenge_runner.py AAPL MSFT NVDA BTC ETH
    python challenge_runner.py --list          # list active challenges only
    python challenge_runner.py --status        # show your challenge portfolios
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from config.settings import load_settings
from core.enums import Decision
from core.models import AnalysisContext
from data.chart_renderer import render_chart
from data.ai4trade_client import AI4TradeClient
from data.market_intel_source import CombinedNewsSource, MarketIntelNewsSource
from data.news_sources import AlpacaNewsSource
from agents.fundamental_agent import FundamentalAgent
from agents.liquid_agent import LiquidAgent
from agents.risk_agent import RiskAgent
from agents.social_agent import SocialSentimentAgent
from agents.technical_agent import TechnicalAgent
from agents.vision_agent import VisionAgent
from execution.alpaca_broker import AlpacaBroker
from execution.portfolio_manager import PortfolioManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
logger = logging.getLogger("challenge")

_CRYPTO = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "MATIC", "LINK", "UNI", "AAVE", "XRP"}


def _track(ticker: str) -> str:
    return "crypto" if ticker.upper() in _CRYPTO else "us-stock"


async def list_challenges(ai4: AI4TradeClient) -> None:
    challenges = await ai4.list_challenges(status="active")
    if not challenges:
        print("No active challenges found.")
        return
    print(f"\n{'Key':<30} {'Track':<12} {'Status':<10} {'End':<20}")
    print("-" * 75)
    for c in challenges:
        end = c.get("end_at", "")[:19].replace("T", " ")
        print(f"{c.get('challenge_key',''):<30} {c.get('track',c.get('market','')):<12} {c.get('status',''):<10} {end:<20}")


async def show_status(ai4: AI4TradeClient) -> None:
    joined = await ai4.get_my_challenges()
    if not joined:
        print("Not joined in any challenges yet.")
        return
    for c in joined:
        key = c.get("challenge_key", "")
        portfolio = await ai4.get_challenge_portfolio(key)
        ret = portfolio.get("return_pct", 0)
        dd = portfolio.get("max_drawdown", 0)
        trades = portfolio.get("trade_count", 0)
        rank = c.get("rank", "?")
        print(f"\n{key}")
        print(f"  Return: {ret:+.2f}%  MaxDD: {dd:.2f}%  Trades: {trades}  Rank: {rank}")


async def run_challenge(tickers: list[str]) -> None:
    settings = load_settings()
    ai4 = AI4TradeClient()
    await ai4.__aenter__()

    if not ai4.token:
        logger.error("AI4Trade auth failed — set AI4TRADE_EMAIL and AI4TRADE_PASSWORD in .env")
        return

    broker = AlpacaBroker(settings.alpaca_key_id, settings.alpaca_secret, paper=True)
    news = CombinedNewsSource(
        AlpacaNewsSource(settings.alpaca_key_id, settings.alpaca_secret),
        MarketIntelNewsSource(ai4),
    )
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

    # Find and join relevant active challenges
    all_challenges = await ai4.list_challenges(status="active")
    joined: dict[str, list[str]] = {}  # challenge_key -> [tickers]

    for ticker in tickers:
        track = _track(ticker)
        for ch in all_challenges:
            ch_track = ch.get("track") or ch.get("market") or ""
            if ch_track == track:
                key = ch.get("challenge_key", "")
                if not key:
                    continue
                resp = await ai4.join_challenge(key)
                if resp.get("success") or resp.get("idempotent"):
                    joined.setdefault(key, []).append(ticker)
                    logger.info("Joined challenge %s for %s", key, ticker)

    if not joined:
        logger.warning("No matching challenges found for tickers %s — check ai4trade.ai for active competitions", tickers)

    # Evaluate and submit
    async with broker:
        for ticker in tickers:
            try:
                bars = await broker.get_bars(ticker, timeframe="5Min", limit=200)
                account = await broker.get_account()
                chart = render_chart(ticker, bars)
                ctx = AnalysisContext(ticker=ticker, bars=bars, account=account, chart_image_path=chart)
                decision = await pm.decide(ctx)

                logger.info(
                    "%s -> %s composite=%.1f",
                    ticker, decision.decision.value, decision.composite_score,
                )

                if not decision.is_actionable or not decision.risk:
                    logger.info("  PASS — not submitting to challenge")
                    continue

                side = "buy" if decision.decision is Decision.LONG else "short"
                r = decision.risk
                content = (
                    f"composite={decision.composite_score:.1f} | "
                    + " | ".join(f"{e.role.value}={e.score}" for e in decision.evaluations)
                )

                # Submit to all matching challenges
                for key, challenge_tickers in joined.items():
                    if ticker in challenge_tickers:
                        result = await ai4.submit_challenge_trade(
                            key,
                            side=side,
                            symbol=ticker,
                            price=r.entry,
                            quantity=r.qty,
                            content=content,
                        )
                        ch_portfolio = result.get("portfolio", {})
                        logger.info(
                            "  Challenge %s: return=%.2f%% trades=%d",
                            key,
                            ch_portfolio.get("return_pct", 0),
                            ch_portfolio.get("trade_count", 0),
                        )
            except Exception:
                logger.exception("Challenge eval failed for %s", ticker)

    # Print final standings
    print("\n=== Challenge Standings ===")
    for key in joined:
        portfolio = await ai4.get_challenge_portfolio(key)
        lb = await ai4.get_challenge_leaderboard(key)
        my_rank = next((r.get("rank") for r in lb if r.get("agent_id") == ai4.agent_id), "?")
        print(
            f"{key}: return={portfolio.get('return_pct', 0):+.2f}% "
            f"maxDD={portfolio.get('max_drawdown', 0):.2f}% "
            f"rank={my_rank}/{len(lb)}"
        )

    await ai4.__aexit__(None, None, None)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI4Trade challenge runner")
    parser.add_argument("tickers", nargs="*", help="Ticker symbols")
    parser.add_argument("--list", action="store_true", help="List active challenges and exit")
    parser.add_argument("--status", action="store_true", help="Show your challenge portfolios and exit")
    args = parser.parse_args()

    async def _run():
        ai4 = AI4TradeClient()
        await ai4.__aenter__()
        if args.list:
            await list_challenges(ai4)
        elif args.status:
            await show_status(ai4)
        await ai4.__aexit__(None, None, None)

    if args.list or args.status:
        asyncio.run(_run())
    else:
        tickers = [t.upper() for t in args.tickers] or ["AAPL", "NVDA"]
        asyncio.run(run_challenge(tickers))


if __name__ == "__main__":
    main()
