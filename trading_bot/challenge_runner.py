"""Challenge Competition Runner — AI4Trade benchmarking.

Registers the bot in active AI4Trade trading challenges and auto-submits its
trade decisions for objective benchmarking against other AI agents.

Uses the SAME agent pipeline as live/backtest via bootstrap.build_manager — no
duplicated wiring. Requires AI4TRADE_EMAIL / AI4TRADE_PASSWORD (the same creds
the Social agent needs); without them it exits cleanly.

Challenge track auto-selection:
  - Tickers in _CRYPTO → crypto challenges
  - Everything else    → us-stock challenges

Usage:
    python challenge_runner.py AAPL MSFT NVDA
    python challenge_runner.py --list      # list active challenges only
    python challenge_runner.py --status    # show your challenge portfolios
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from bootstrap import load_env

load_env()

from config.settings import load_settings           # noqa: E402
from core.enums import Decision                      # noqa: E402
from core.models import AnalysisContext              # noqa: E402
from data.chart_renderer import render_chart         # noqa: E402
from data.ai4trade_client import AI4TradeClient      # noqa: E402
from execution.alpaca_broker import AlpacaBroker     # noqa: E402
from bootstrap import build_manager                  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
logger = logging.getLogger("challenge")

_RESULTS_FILE = Path(__file__).parent.parent / "challenge_results.json"
_DEFAULT_TICKERS = ["AAPL", "NVDA", "TSLA", "MSFT", "AMD"]
_CRYPTO = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "MATIC", "LINK", "UNI", "AAVE", "XRP"}


def _track(ticker: str) -> str:
    return "crypto" if ticker.upper() in _CRYPTO else "us-stock"


def _write_results(payload: dict) -> None:
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        _RESULTS_FILE.write_text(json.dumps(payload, indent=2))
    except Exception:
        logger.exception("Could not write challenge results")


async def list_challenges(ai4: AI4TradeClient) -> list[dict]:
    challenges = await ai4.list_challenges(status="active")
    rows = [
        {
            "challenge_key": c.get("challenge_key", ""),
            "track":         c.get("track", c.get("market", "")),
            "status":        c.get("status", ""),
            "end_at":        c.get("end_at", ""),
        }
        for c in (challenges or [])
    ]
    _write_results({"mode": "list", "challenges": rows})
    for r in rows:
        print(f"{r['challenge_key']:<30} {r['track']:<12} {r['status']:<10} {r['end_at'][:19]}")
    if not rows:
        print("No active challenges found.")
    return rows


async def show_status(ai4: AI4TradeClient) -> list[dict]:
    joined = await ai4.get_my_challenges()
    standings = []
    for c in (joined or []):
        key = c.get("challenge_key", "")
        portfolio = await ai4.get_challenge_portfolio(key)
        standings.append({
            "challenge_key": key,
            "return_pct":    portfolio.get("return_pct", 0),
            "max_drawdown":  portfolio.get("max_drawdown", 0),
            "trade_count":   portfolio.get("trade_count", 0),
            "rank":          c.get("rank", "?"),
        })
    _write_results({"mode": "status", "standings": standings})
    for s in standings:
        print(f"{s['challenge_key']}: return={s['return_pct']:+.2f}% "
              f"maxDD={s['max_drawdown']:.2f}% trades={s['trade_count']} rank={s['rank']}")
    if not standings:
        print("Not joined in any challenges yet.")
    return standings


async def run_challenge(tickers: list[str]) -> None:
    settings = load_settings()
    if not settings.alpaca_key_id or not settings.alpaca_secret:
        logger.error("Set ALPACA_API_KEY_ID and ALPACA_API_SECRET")
        _write_results({"mode": "run", "error": "missing Alpaca credentials", "standings": []})
        return

    ai4 = AI4TradeClient()
    await ai4.__aenter__()
    try:
        if not ai4.token:
            logger.error("AI4Trade auth failed — set AI4TRADE_EMAIL and AI4TRADE_PASSWORD")
            _write_results({"mode": "run", "error": "AI4Trade auth failed — set AI4TRADE_EMAIL/PASSWORD",
                            "standings": []})
            return

        broker = AlpacaBroker(settings.alpaca_key_id, settings.alpaca_secret, paper=True)
        # Same composition as live/backtest — full agent pipeline incl. social.
        pm = build_manager(settings, broker, ai4)

        # Join relevant active challenges
        all_challenges = await ai4.list_challenges(status="active")
        joined: dict[str, list[str]] = {}
        for ticker in tickers:
            track = _track(ticker)
            for ch in (all_challenges or []):
                if (ch.get("track") or ch.get("market") or "") != track:
                    continue
                key = ch.get("challenge_key", "")
                if not key:
                    continue
                resp = await ai4.join_challenge(key)
                if resp.get("success") or resp.get("idempotent"):
                    joined.setdefault(key, []).append(ticker)
                    logger.info("Joined challenge %s for %s", key, ticker)

        if not joined:
            logger.warning("No matching active challenges for %s", tickers)

        submissions: list[dict] = []
        async with broker:
            for ticker in tickers:
                try:
                    bars = await broker.get_bars(ticker, timeframe="5Min", limit=200)
                    account = await broker.get_account()
                    chart = render_chart(ticker, bars)
                    ctx = AnalysisContext(ticker=ticker, bars=bars, account=account,
                                          chart_image_path=chart)
                    decision = await pm.decide(ctx)
                    logger.info("%s -> %s composite=%.1f",
                                ticker, decision.decision.value, decision.composite_score)

                    if not decision.is_actionable or not decision.risk:
                        continue

                    side = "buy" if decision.decision is Decision.LONG else "short"
                    r = decision.risk
                    content = (f"composite={decision.composite_score:.1f} | "
                               + " | ".join(f"{e.role.value}={e.score}" for e in decision.evaluations))
                    for key, ch_tickers in joined.items():
                        if ticker in ch_tickers:
                            res = await ai4.submit_challenge_trade(
                                key, side=side, symbol=ticker,
                                price=r.entry, quantity=r.qty, content=content,
                            )
                            submissions.append({
                                "challenge_key": key, "ticker": ticker, "side": side,
                                "composite": round(decision.composite_score, 1),
                            })
                            logger.info("  submitted %s %s to %s", side, ticker, key)
                except Exception:
                    logger.exception("Challenge eval failed for %s", ticker)

        # Final standings
        standings = []
        for key in joined:
            portfolio = await ai4.get_challenge_portfolio(key)
            lb = await ai4.get_challenge_leaderboard(key)
            my_rank = next((row.get("rank") for row in (lb or [])
                            if row.get("agent_id") == ai4.agent_id), "?")
            standings.append({
                "challenge_key": key,
                "return_pct":    portfolio.get("return_pct", 0),
                "max_drawdown":  portfolio.get("max_drawdown", 0),
                "rank":          my_rank,
                "field_size":    len(lb or []),
            })
            print(f"{key}: return={portfolio.get('return_pct', 0):+.2f}% rank={my_rank}/{len(lb or [])}")

        _write_results({
            "mode":        "run",
            "tickers":     tickers,
            "submissions": submissions,
            "standings":   standings,
        })
    finally:
        await ai4.__aexit__(None, None, None)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI4Trade challenge runner")
    parser.add_argument("tickers", nargs="*", help="Ticker symbols")
    parser.add_argument("--list", action="store_true", help="List active challenges and exit")
    parser.add_argument("--status", action="store_true", help="Show your challenge portfolios and exit")
    args = parser.parse_args()

    async def _run_meta():
        ai4 = AI4TradeClient()
        await ai4.__aenter__()
        try:
            if not ai4.token:
                logger.error("AI4Trade auth failed — set AI4TRADE_EMAIL and AI4TRADE_PASSWORD")
                _write_results({"mode": "list" if args.list else "status",
                                "error": "AI4Trade auth failed — set AI4TRADE_EMAIL/PASSWORD"})
                return
            if args.list:
                await list_challenges(ai4)
            else:
                await show_status(ai4)
        finally:
            await ai4.__aexit__(None, None, None)

    if args.list or args.status:
        asyncio.run(_run_meta())
    else:
        tickers = [t.upper() for t in args.tickers] or _DEFAULT_TICKERS
        asyncio.run(run_challenge(tickers))


if __name__ == "__main__":
    main()
