"""30-day walk-forward backtest — day-trade focused.

Usage (from trading_bot/ directory):
    python backtest_30day.py
    python backtest_30day.py --days 30 --tickers NVDA TSLA AAPL MSFT
    python backtest_30day.py --days 14 --top 15

What it does:
  1. Fetches 30 days of 5-min bars for each ticker via Alpaca REST.
  2. Every ~half-trading-day, runs the full agent pipeline (Technical + Fundamental + Risk).
  3. Simulates bracket orders: TP / SL / intraday-close (forces exit at 15:55 ET).
  4. Prints a summary table + writes backtest_results.json.

Day-trade rule: every position opened is closed by end of same calendar day.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Search for .env in: trading_bot/ -> project root -> dashboard/
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

# -- path fix so we can import trading_bot packages ----------------------------
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import load_settings
from core.enums import Decision
from core.models import AnalysisContext, RiskParameters
from agents.technical_agent import TechnicalAgent
from agents.fundamental_agent import FundamentalAgent
from agents.risk_agent import RiskAgent
from execution.portfolio_manager import PortfolioManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bt30")

RESULTS_FILE = Path(__file__).parent.parent / "backtest_results.json"

# -- Data model ----------------------------------------------------------------

@dataclass
class TradeResult:
    ticker:      str
    direction:   str
    entry_time:  str
    exit_time:   str
    entry_price: float
    exit_price:  float
    qty:         float
    stop_loss:   float
    take_profit: float
    risk_reward: float
    outcome:     str   # TP_HIT | SL_HIT | EOD_CLOSE
    pnl_usd:     float
    pnl_pct:     float
    score:       float


# -- Alpaca historical bars ----------------------------------------------------

async def fetch_bars_range(
    symbol: str,
    start: datetime,
    end: datetime,
    key_id: str,
    secret: str,
    timeframe: str = "5Min",
) -> Optional[pd.DataFrame]:
    """Fetch date-range bars from Alpaca REST v2 with pagination."""
    import aiohttp

    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    headers = {
        "APCA-API-KEY-ID":     key_id,
        "APCA-API-SECRET-KEY": secret,
    }
    params = {
        "timeframe": timeframe,
        "start":     start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end":       end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "feed":      "iex",
        "adjustment": "raw",
        "limit":     10000,
    }

    all_bars: list[dict] = []
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            try:
                async with session.get(url, params=params,
                                       timeout=aiohttp.ClientTimeout(total=30)) as r:
                    if r.status == 403:
                        logger.warning("%s: 403 -- subscription limit, skipping", symbol)
                        return None
                    r.raise_for_status()
                    data = await r.json()
            except Exception as exc:
                logger.warning("fetch_bars_range error for %s: %s", symbol, exc)
                return None

            bars = data.get("bars", [])
            all_bars.extend(bars)
            next_token = data.get("next_page_token")
            if not next_token or not bars:
                break
            params["page_token"] = next_token

    if not all_bars:
        logger.warning("%s: no bars returned", symbol)
        return None

    df = pd.DataFrame(all_bars)
    df = df.rename(columns={"t": "timestamp", "o": "open", "h": "high",
                             "l": "low", "c": "close", "v": "volume"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    logger.info("  %s: fetched %d bars (%s -> %s)",
                symbol, len(df),
                df.index[0].date(), df.index[-1].date())
    return df


# -- Fill simulator (day-trade: forced close at 15:55 ET) ----------------------

_ET = timezone(timedelta(hours=-4))   # EDT (UTC-4) -- use -5 in winter

def simulate_day_trade(
    future_bars: pd.DataFrame,
    *,
    direction: Decision,
    entry: float,
    stop_loss: float,
    take_profit: float,
    qty: float,
) -> tuple[str, float, str, float, float]:
    """Walk forward bars; force exit by 15:55 ET same calendar day."""
    entry_date = future_bars.index[0].astimezone(_ET).date() if len(future_bars) else date.today()
    mult = 1 if direction is Decision.LONG else -1

    for ts, bar in future_bars.iterrows():
        bar_date = ts.astimezone(_ET).date()
        bar_time = ts.astimezone(_ET).time()

        # Force EOD close at/after 15:55 on same day, or next day open
        if bar_date > entry_date or (bar_date == entry_date and bar_time.hour == 15 and bar_time.minute >= 55):
            exit_px = float(bar["open"])
            pnl = mult * (exit_px - entry) * qty
            return "EOD_CLOSE", exit_px, str(ts), pnl, mult * (exit_px - entry) / entry * 100

        high = float(bar["high"])
        low  = float(bar["low"])

        if direction is Decision.LONG:
            sl_hit = low  <= stop_loss
            tp_hit = high >= take_profit
        else:
            sl_hit = high >= stop_loss
            tp_hit = low  <= take_profit

        if tp_hit and sl_hit:
            # Both triggered same bar -> worst case SL
            exit_px = stop_loss
            pnl = mult * (exit_px - entry) * qty
            return "SL_HIT", exit_px, str(ts), pnl, mult * (exit_px - entry) / entry * 100
        elif tp_hit:
            exit_px = take_profit
            pnl = mult * (exit_px - entry) * qty
            return "TP_HIT", exit_px, str(ts), pnl, mult * (exit_px - entry) / entry * 100
        elif sl_hit:
            exit_px = stop_loss
            pnl = mult * (exit_px - entry) * qty
            return "SL_HIT", exit_px, str(ts), pnl, mult * (exit_px - entry) / entry * 100

    # Fallback: use last bar's close
    last_ts  = future_bars.index[-1]
    last_px  = float(future_bars["close"].iloc[-1])
    pnl = mult * (last_px - entry) * qty
    return "EOD_CLOSE", last_px, str(last_ts), pnl, mult * (last_px - entry) / entry * 100


# -- Walk-forward for one ticker -----------------------------------------------

LOOKBACK_BARS = 200   # bars fed to agents
STEP_BARS     = 26    # evaluate every ~2 hours (26 x 5min = 130min)
# Entry filter: skip evaluations where the entry bar falls after this UTC hour.
# 19:00 UTC = 15:00 ET -- no new entries in the last hour of RTH.
ENTRY_CUTOFF_UTC_HOUR = 19

# -- Research-derived entry filters --------------------------------------------
# Research #1 (Luo et al. 2023 / PEAD): skip the first 30 min of RTH.
OPEN_NOISE_UTC_HOUR   = 13   # 13:xx UTC = 9:xx ET
OPEN_NOISE_UTC_MINUTE = 30
OPEN_NOISE_END_MINUTE = 60

# Research #3 (Barber & Odean): minimum session volume confirmation ratio.
VOLUME_CONFIRM_RATIO = 1.3


async def backtest_ticker(
    pm: PortfolioManager,
    ticker: str,
    bars: pd.DataFrame,
) -> list[TradeResult]:
    results: list[TradeResult] = []
    n = len(bars)
    sl_dates: set[date] = set()

    # ── Pre-compute vol_ratio tables once (O(n)), avoids O(n²) groupby in loop.
    _b_dates = [ts.astimezone(_ET).date() for ts in bars.index]
    _b_date_s = pd.Series(_b_dates, index=bars.index, dtype=object)
    _daily_vol: dict = bars.groupby(_b_date_s)["volume"].sum().to_dict()
    _sorted_days = sorted(_daily_vol.keys())
    _tmp = bars[["volume"]].copy()
    _tmp["_d"] = _b_dates
    _tmp["_cv"] = _tmp.groupby("_d")["volume"].cumsum()
    _tmp["_bc"] = _tmp.groupby("_d").cumcount()   # 0-based within day
    _cumvol: dict = _tmp["_cv"].to_dict()
    _barcnt: dict = _tmp["_bc"].to_dict()

    def _vol_ratio(entry_ts: pd.Timestamp, today_d) -> float:
        loc = bars.index.searchsorted(entry_ts, side="left") - 1
        if loc < 0 or _b_date_s.iloc[loc] != today_d:
            return 1.0
        prev_ts   = bars.index[loc]
        vol_sofar = float(_cumvol.get(prev_ts, 0.0))
        bar_cnt   = int(_barcnt.get(prev_ts, 0)) + 1
        frac      = min(bar_cnt / 78.0, 1.0)
        if frac < 0.05:
            return 1.0
        prior = [d for d in _sorted_days if d < today_d]
        if not prior:
            return 1.0
        tail = prior[-20:]
        avg_daily = sum(_daily_vol[d] for d in tail) / len(tail)
        return (vol_sofar / frac) / avg_daily if avg_daily > 0 else 1.0

    for i in range(LOOKBACK_BARS, n - 2, STEP_BARS):
        window = bars.iloc[i - LOOKBACK_BARS: i]
        entry_bar_idx = i
        entry_ts = bars.index[entry_bar_idx]

        if entry_ts.astimezone(timezone.utc).hour >= ENTRY_CUTOFF_UTC_HOUR:
            continue

        # Research #1 (PEAD): skip 9:30-10:00 ET open noise
        entry_et = entry_ts.astimezone(_ET)
        if entry_et.hour == 9:
            continue

        entry_date_et = entry_et.date()
        if entry_date_et in sl_dates:
            continue

        # Research #3: session volume confirmation (O(1) lookup)
        if _vol_ratio(entry_ts, entry_date_et) < VOLUME_CONFIRM_RATIO:
            continue

        ctx = AnalysisContext(
            ticker=ticker,
            bars=window,
            account={"equity": 100_000.0, "buying_power": 50_000.0},
        )

        try:
            decision = await pm.decide(ctx)
        except Exception as exc:
            logger.warning("decide() failed for %s at %s: %s", ticker, entry_ts, exc)
            continue

        if not decision.is_actionable or decision.risk is None:
            continue

        r = decision.risk
        # Entry at next bar's open
        if entry_bar_idx + 1 >= n:
            continue
        entry_price = float(bars["open"].iloc[entry_bar_idx + 1])

        # Recalculate SL/TP relative to actual fill price (keep ATR distances)
        sl_dist = abs(entry_price - r.stop_loss)
        tp_dist = abs(r.take_profit - entry_price)
        if decision.decision is Decision.LONG:
            sl = entry_price - sl_dist
            tp = entry_price + tp_dist
        else:
            sl = entry_price + sl_dist
            tp = entry_price - tp_dist

        future = bars.iloc[entry_bar_idx + 1:]
        outcome, exit_px, exit_ts, pnl, pnl_pct = simulate_day_trade(
            future,
            direction=decision.decision,
            entry=entry_price,
            stop_loss=sl,
            take_profit=tp,
            qty=float(r.qty),
        )

        results.append(TradeResult(
            ticker      = ticker,
            direction   = decision.decision.value,
            entry_time  = str(entry_ts),
            exit_time   = exit_ts,
            entry_price = round(entry_price, 4),
            exit_price  = round(exit_px, 4),
            qty         = float(r.qty),
            stop_loss   = round(sl, 4),
            take_profit = round(tp, 4),
            risk_reward = round(tp_dist / sl_dist, 3) if sl_dist > 0 else 0,
            outcome     = outcome,
            pnl_usd     = round(pnl, 2),
            pnl_pct     = round(pnl_pct, 4),
            score       = round(float(decision.composite_score), 1),
        ))

        if outcome == "SL_HIT":
            sl_dates.add(entry_date_et)

    return results


# -- Summary stats -------------------------------------------------------------

def calc_summary(all_trades: list[TradeResult]) -> dict:
    """Compute backtest stats dict. Used by both backtest and optimizer."""
    if not all_trades:
        return {}

    df = pd.DataFrame([asdict(t) for t in all_trades])
    total = len(df)
    wins   = int((df["outcome"] == "TP_HIT").sum())
    losses = int((df["outcome"] == "SL_HIT").sum())
    eods   = int((df["outcome"] == "EOD_CLOSE").sum())

    win_rate    = wins / total * 100 if total else 0.0
    total_pnl   = float(df["pnl_usd"].sum())
    gross_wins  = float(df.loc[df["pnl_usd"] > 0, "pnl_usd"].sum())
    gross_loss  = abs(float(df.loc[df["pnl_usd"] < 0, "pnl_usd"].sum()))
    profit_factor = gross_wins / gross_loss if gross_loss > 0 else float("inf")

    avg_win  = float(df.loc[df["outcome"] == "TP_HIT",    "pnl_usd"].mean()) if wins  else 0.0
    avg_loss = float(df.loc[df["outcome"] == "SL_HIT",    "pnl_usd"].mean()) if losses else 0.0

    df_sorted = df.sort_values("entry_time")
    daily = df_sorted.groupby(df_sorted["entry_time"].str[:10])["pnl_usd"].sum()
    sharpe = float(daily.mean() / daily.std() * (252 ** 0.5)) if len(daily) > 1 else 0.0

    cum = df_sorted["pnl_usd"].cumsum()
    max_dd = float((cum - cum.cummax()).min())

    ev_per_trade = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    by_tk = df.groupby("ticker").agg(
        trades=("pnl_usd", "count"),
        pnl=("pnl_usd", "sum"),
        win_rate=("outcome", lambda x: (x == "TP_HIT").mean() * 100),
    ).sort_values("pnl", ascending=False)

    return {
        "total_trades":   total,
        "wins":           wins,
        "losses":         losses,
        "eods":           eods,
        "win_rate":       round(win_rate, 2),
        "total_pnl":      round(total_pnl, 2),
        "avg_win":        round(avg_win, 2),
        "avg_loss":       round(avg_loss, 2),
        "profit_factor":  round(profit_factor, 3),
        "sharpe":         round(sharpe, 3),
        "max_drawdown":   round(max_dd, 2),
        "ev_per_trade":   round(ev_per_trade, 2),
        "by_ticker":      by_tk.reset_index().to_dict(orient="records"),
        "trades":         [asdict(t) for t in all_trades],
    }


def print_summary(all_trades: list[TradeResult]) -> dict:
    """Compute + pretty-print backtest stats. Returns the stats dict."""
    summary = calc_summary(all_trades)
    if not summary:
        print("\n  No trades were generated. Check API keys and data availability.\n")
        return {}

    total  = summary["total_trades"]
    wins   = summary["wins"]
    losses = summary["losses"]
    eods   = summary["eods"]

    sep = "-" * 52
    print("\n" + "=" * 52)
    print("  Backtest Summary -- Day Trades Only")
    print("=" * 52)
    print(f"  Total trades  : {total}")
    print(f"  TP Hit        : {wins}  ({summary['win_rate']:.1f}%)")
    print(f"  SL Hit        : {losses}")
    print(f"  EOD Close     : {eods}")
    print(sep)
    print(f"  Total P&L     : ${summary['total_pnl']:+.2f}")
    print(f"  EV / trade    : ${summary['ev_per_trade']:+.2f}")
    print(f"  Avg Win       : ${summary['avg_win']:+.2f}")
    print(f"  Avg Loss      : ${summary['avg_loss']:+.2f}")
    print(f"  Profit Factor : {summary['profit_factor']:.2f}")
    print(f"  Sharpe Ratio  : {summary['sharpe']:.2f}")
    print(f"  Max Drawdown  : ${summary['max_drawdown']:.2f}")
    print(sep)

    df = pd.DataFrame([asdict(t) for t in all_trades])
    by_tk = df.groupby("ticker").agg(
        trades=("pnl_usd", "count"),
        pnl=("pnl_usd", "sum"),
        win_rate=("outcome", lambda x: (x == "TP_HIT").mean() * 100),
    ).sort_values("pnl", ascending=False)

    print(f"  {'Ticker':<8} {'Trades':>6} {'Win%':>6} {'P&L':>10}")
    print(f"  {sep}")
    for tk, row in by_tk.iterrows():
        marker = "+" if row["pnl"] > 0 else "-"
        print(f"  {marker} {tk:<6} {int(row['trades']):>6} {row['win_rate']:>5.1f}% {row['pnl']:>+10.2f}")
    print("=" * 52 + "\n")

    return summary


# -- Main ----------------------------------------------------------------------

DEFAULT_TICKERS = [
    "NVDA", "TSLA", "AAPL", "MSFT", "AMD",
    "META", "AMZN", "GOOGL", "SPY", "QQQ",
]


async def run(tickers: list[str], days: int) -> None:
    settings = load_settings()

    if not settings.alpaca_key_id or not settings.alpaca_secret:
        logger.error("Set ALPACA_API_KEY_ID and ALPACA_API_SECRET in .env")
        return

    end_dt   = datetime.now(tz=timezone.utc).replace(hour=23, minute=59, second=59)
    start_dt = end_dt - timedelta(days=days + 5)   # buffer for weekends

    logger.info("Backtest window: %s -> %s  (%d days)",
                start_dt.date(), end_dt.date(), days)

    fetch_list = list(dict.fromkeys(tickers + ["SPY"]))
    logger.info("Tickers: %s (+ SPY for RS signal)", " ".join(tickers))

    all_bars: dict[str, pd.DataFrame] = {}
    for ticker in fetch_list:
        logger.info("Fetching %d days of 5-min bars for %s...", days, ticker)
        bars = await fetch_bars_range(
            ticker, start_dt, end_dt,
            settings.alpaca_key_id, settings.alpaca_secret,
        )
        if bars is not None and len(bars) >= LOOKBACK_BARS + 10:
            all_bars[ticker] = bars
        else:
            logger.warning("Skipping %s -- not enough data", ticker)

    import os
    os.environ.setdefault("LONG_THRESHOLD",    "65")
    os.environ.setdefault("SHORT_THRESHOLD",   "35")
    os.environ.setdefault("ATR_STOP_MULTIPLE", "2.0")
    os.environ.setdefault("ATR_TARGET_MULTIPLE", "4.0")

    settings = load_settings()

    technical_agent = TechnicalAgent(weight=settings.weights.technical)

    if "SPY" in all_bars:
        technical_agent.spy_bars = all_bars["SPY"]
        logger.info("SPY bars injected into TechnicalAgent (%d bars)", len(all_bars["SPY"]))

    pm = PortfolioManager(
        settings=settings,
        broker=None,
        fundamental=FundamentalAgent(
            news_source=None,
            weight=settings.weights.fundamental,
            anthropic_api_key=settings.anthropic_api_key,
            model=settings.llm_model,
        ),
        vision=None,
        technical=technical_agent,
        risk=RiskAgent(settings.risk),
    )

    all_trades: list[TradeResult] = []

    for ticker in tickers:
        if ticker not in all_bars:
            continue
        bars = all_bars[ticker]
        logger.info("Running walk-forward for %s (%d bars)...", ticker, len(bars))
        trades = await backtest_ticker(pm, ticker, bars)
        all_trades.extend(trades)
        logger.info("%s done: %d trades, P&L=$%.2f",
                    ticker, len(trades), sum(t.pnl_usd for t in trades))

    summary = print_summary(all_trades)

    if summary:
        RESULTS_FILE.write_text(json.dumps(summary, indent=2, default=str))
        logger.info("Results saved to %s", RESULTS_FILE)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="30-day intraday backtest")
    parser.add_argument("--days",    type=int, default=30, help="Lookback days (default 30)")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS,
                        help="Ticker list (default: top 10 liquid stocks)")
    parser.add_argument("--top",     type=int, default=0,
                        help="Use top N from universe scanner instead of --tickers")
    args = parser.parse_args()

    async def _run():
        tickers = [t.upper() for t in args.tickers]

        if args.top > 0:
            from data.universe_scanner import UniverseScanner
            settings = load_settings()
            scanner = UniverseScanner(settings.alpaca_key_id, settings.alpaca_secret)
            logger.info("Scanning universe for top %d candidates...", args.top)
            tickers = await scanner.get_candidates(top_n=args.top)
            logger.info("Universe: %s", " ".join(tickers))

        await run(tickers, args.days)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
