"""Sector Scanner — hot sector detection and intra-sector stock ranking.

Usage (called once per scan cycle in main.py / live_runner.py):

    scanner = SectorScanner(broker)
    report  = await scanner.scan(tickers)

    # Skip tickers in cold sectors
    hot = report.hot_tickers(top_n_sectors=2)

The scanner fetches today's OHLCV for every ticker concurrently, computes
a sector momentum score (avg relative-strength + avg volume-surge), and
returns both a sector leaderboard and per-ticker ranks.

Sector mapping falls back to a built-in static dict when live classification
is not available.  Add your own tickers to SECTOR_MAP as needed.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── Static fallback sector map ───────────────────────────────────────────────
SECTOR_MAP: Dict[str, str] = {
    # Technology
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "AMD":  "Technology", "INTC": "Technology", "AVGO": "Technology",
    "QCOM": "Technology", "MU":   "Technology", "TSM":  "Technology",
    "ORCL": "Technology", "CRM":  "Technology", "ADBE": "Technology",
    "PLTR": "Technology", "MRVL": "Technology",
    # Consumer Discretionary
    "AMZN": "Consumer", "TSLA": "Consumer", "HD": "Consumer",
    "NKE":  "Consumer", "MCD":  "Consumer", "SBUX": "Consumer",
    # Financials
    "JPM": "Financials", "BAC": "Financials", "GS": "Financials",
    "MS":  "Financials", "V":   "Financials", "MA": "Financials",
    "C":   "Financials", "WFC": "Financials",
    # Energy
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy", "SLB": "Energy",
    # Healthcare
    "JNJ": "Healthcare", "PFE": "Healthcare", "UNH": "Healthcare",
    "ABBV": "Healthcare", "MRK": "Healthcare",
    # Communication
    "GOOGL": "Communication", "META": "Communication",
    "NFLX":  "Communication", "DIS":  "Communication",
    # ETFs
    "SPY": "ETF", "QQQ": "ETF", "IWM": "ETF", "VIX": "ETF", "VIXY": "ETF",
}


def _get_sector(ticker: str) -> str:
    return SECTOR_MAP.get(ticker.upper(), "Other")


def _day_chg_pct(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    today = df.index[-1].date()
    today_df = df[df.index.map(lambda x: x.date()) == today]
    if today_df.empty:
        today_df = df.tail(78)
    open_px = float(today_df["open"].iloc[0])
    last_px = float(today_df["close"].iloc[-1])
    return (last_px - open_px) / open_px * 100 if open_px else 0.0


def _vol_ratio(df: pd.DataFrame) -> float:
    """Current session projected volume / 20-day avg daily volume."""
    if df.empty or len(df) < 20:
        return 1.0
    today = df.index[-1].date()
    today_df = df[df.index.map(lambda x: x.date()) == today]
    prior    = df[df.index.map(lambda x: x.date()) < today]
    if today_df.empty or prior.empty:
        return 1.0
    cum = float(today_df["volume"].sum())
    bars_per_day = 78
    frac = min(len(today_df) / bars_per_day, 1.0)
    projected = cum / frac if frac > 0.05 else cum
    avg_daily = float(prior.groupby(prior.index.date)["volume"].sum().tail(20).mean())
    return projected / avg_daily if avg_daily > 0 else 1.0


@dataclass
class TickerStats:
    ticker:      str
    sector:      str
    day_chg:     float = 0.0   # % from open
    vol_ratio:   float = 1.0   # projected vol / 20-day avg
    rs_vs_spy:   Optional[float] = None  # relative strength ratio
    score:       float = 0.0   # composite momentum score
    sector_rank: int   = 0     # rank within sector (1 = hottest)


@dataclass
class ScanReport:
    stats:          Dict[str, TickerStats]   # ticker → TickerStats
    sector_scores:  Dict[str, float]         # sector → avg score
    sector_ranks:   Dict[str, int]           # sector → rank (1 = hottest)

    def hot_tickers(self, top_n_sectors: int = 2) -> List[str]:
        """Return tickers that belong to the top N sectors by momentum."""
        top_sectors = {
            s for s, _ in sorted(
                self.sector_scores.items(), key=lambda x: x[1], reverse=True
            )[:top_n_sectors]
        }
        # Also always include ETF-sector tickers (SPY, QQQ) for context
        top_sectors.add("ETF")
        return [t for t, st in self.stats.items() if st.sector in top_sectors]

    def sector_summary(self) -> str:
        rows = sorted(self.sector_scores.items(), key=lambda x: x[1], reverse=True)
        return " | ".join(f"{s}:{sc:.1f}" for s, sc in rows)


class SectorScanner:
    def __init__(self, broker) -> None:
        self.broker = broker

    async def scan(self, tickers: List[str]) -> ScanReport:
        """Fetch bars for all tickers concurrently and build the report."""
        # Ensure SPY is included for RS computation
        fetch_set = list({t.upper() for t in tickers} | {"SPY"})
        tasks = {
            t: self.broker.get_bars(t, timeframe="5Min", limit=120)
            for t in fetch_set
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        bars_map: Dict[str, pd.DataFrame] = {}
        for ticker, result in zip(tasks.keys(), results):
            if isinstance(result, pd.DataFrame):
                bars_map[ticker] = result
            else:
                logger.debug("scan: %s fetch failed: %s", ticker, result)

        spy_df = bars_map.get("SPY", pd.DataFrame())
        spy_chg = _day_chg_pct(spy_df)

        # ── Per-ticker stats ──────────────────────────────────────────────
        stats: Dict[str, TickerStats] = {}
        for ticker in tickers:
            t = ticker.upper()
            df = bars_map.get(t, pd.DataFrame())
            sector = _get_sector(t)
            if df.empty:
                stats[t] = TickerStats(ticker=t, sector=sector)
                continue

            day_chg  = _day_chg_pct(df)
            vol_rat  = _vol_ratio(df)

            rs = None
            if not spy_df.empty and abs(spy_chg) >= 0.01:
                rs = (1 + day_chg / 100) / (1 + spy_chg / 100)

            # Composite momentum score (0-100 range, 50 = neutral)
            chg_score = float(50 + day_chg * 8)            # ±8 pts per 1% move
            vol_score = float(50 + (vol_rat - 1.0) * 20)   # +20 per 2× avg vol
            rs_score  = float(50 + (rs - 1.0) * 40) if rs is not None else 50.0
            composite = (chg_score + vol_score + rs_score) / 3

            stats[t] = TickerStats(
                ticker=t, sector=sector, day_chg=day_chg,
                vol_ratio=vol_rat, rs_vs_spy=rs, score=composite,
            )

        # ── Sector scores + ranks ─────────────────────────────────────────
        sector_groups: Dict[str, List[float]] = defaultdict(list)
        for st in stats.values():
            sector_groups[st.sector].append(st.score)

        sector_scores = {s: float(sum(v) / len(v)) for s, v in sector_groups.items()}
        ranked_sectors = sorted(sector_scores, key=lambda s: sector_scores[s], reverse=True)
        sector_ranks   = {s: i + 1 for i, s in enumerate(ranked_sectors)}

        # ── Intra-sector ranks ────────────────────────────────────────────
        sector_members: Dict[str, List[TickerStats]] = defaultdict(list)
        for st in stats.values():
            sector_members[st.sector].append(st)
        for members in sector_members.values():
            members.sort(key=lambda x: x.score, reverse=True)
            for rank, st in enumerate(members, start=1):
                stats[st.ticker].sector_rank = rank

        report = ScanReport(
            stats=stats,
            sector_scores=sector_scores,
            sector_ranks=sector_ranks,
        )
        logger.info(
            "SECTORS: %s", report.sector_summary()
        )
        return report
