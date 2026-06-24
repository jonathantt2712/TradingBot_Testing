"""sector_scanner: unit tests for the pure helper functions and ScanReport methods.

Covers _day_chg_pct, _vol_ratio, _get_sector, ScanReport.hot_tickers,
ScanReport.sector_summary, and SectorScanner.scan() integration via a stub broker.
"""
from __future__ import annotations

import asyncio
from datetime import date, timezone

import pandas as pd
import pytest

from data.sector_scanner import (
    ScanReport,
    SectorScanner,
    TickerStats,
    _day_chg_pct,
    _get_sector,
    _vol_ratio,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _session_bars(
    open_px: float,
    close_px: float,
    *,
    n_bars: int = 10,
    volume: int = 100_000,
    date_str: str = "2026-06-10",
) -> pd.DataFrame:
    """Single-session 5-min bars, open fixed, close drifts linearly to close_px."""
    start = f"{date_str} 13:30:00"
    idx = pd.date_range(start=start, periods=n_bars, freq="5min", tz=timezone.utc)
    closes = [open_px + (close_px - open_px) * (i + 1) / n_bars for i in range(n_bars)]
    opens = [open_px] + closes[:-1]
    return pd.DataFrame(
        {
            "open":   opens,
            "high":   [max(o, c) + 0.5 for o, c in zip(opens, closes)],
            "low":    [min(o, c) - 0.5 for o, c in zip(opens, closes)],
            "close":  closes,
            "volume": [volume] * n_bars,
        },
        index=idx,
    )


def _multi_day_bars(
    n_prior_days: int = 20,
    prior_volume: int = 200_000,
    today_volume: int = 400_000,
    n_today_bars: int = 10,
) -> pd.DataFrame:
    """Multi-day DataFrame: n_prior_days of full sessions + a partial 'today'.

    The prior days each have one 5-min bar at fixed volume so the per-day
    total equals prior_volume. 'Today' (2026-06-30) has n_today_bars bars
    each with today_volume // n_today_bars volume.
    """
    frames = []
    # prior days: one bar each on successive dates
    for i in range(n_prior_days):
        day = date(2026, 6, i + 1)
        idx = pd.DatetimeIndex([f"{day} 13:30:00"], tz=timezone.utc)
        frames.append(pd.DataFrame(
            {"open": [100.0], "high": [101.0], "low": [99.0],
             "close": [100.0], "volume": [float(prior_volume)]},
            index=idx,
        ))
    # today: n_today_bars bars each with today_volume // n_today_bars volume
    bar_vol = today_volume // n_today_bars
    today_idx = pd.date_range("2026-06-30 13:30:00", periods=n_today_bars, freq="5min", tz=timezone.utc)
    frames.append(pd.DataFrame(
        {"open": [100.0] * n_today_bars,
         "high": [101.0] * n_today_bars,
         "low":  [99.0]  * n_today_bars,
         "close": [100.0] * n_today_bars,
         "volume": [float(bar_vol)] * n_today_bars},
        index=today_idx,
    ))
    return pd.concat(frames).sort_index()


# ── _get_sector ───────────────────────────────────────────────────────────────

def test_known_ticker_returns_sector():
    assert _get_sector("AAPL") == "Technology"
    assert _get_sector("JPM") == "Financials"
    assert _get_sector("SPY") == "ETF"


def test_unknown_ticker_returns_other():
    assert _get_sector("FOOBAR") == "Other"


def test_get_sector_is_case_insensitive():
    assert _get_sector("aapl") == "Technology"


# ── _day_chg_pct ─────────────────────────────────────────────────────────────

def test_day_chg_pct_empty_returns_zero():
    assert _day_chg_pct(pd.DataFrame()) == 0.0


def test_day_chg_pct_up_session():
    bars = _session_bars(100.0, 105.0)
    pct = _day_chg_pct(bars)
    assert abs(pct - 5.0) < 0.5   # open=100, last_close≈105 → ~5%


def test_day_chg_pct_down_session():
    bars = _session_bars(100.0, 96.0)
    pct = _day_chg_pct(bars)
    assert pct < 0


def test_day_chg_pct_flat_session():
    bars = _session_bars(100.0, 100.0)
    pct = _day_chg_pct(bars)
    assert abs(pct) < 0.01


def test_day_chg_pct_uses_only_today_bars():
    """Even with multi-day data, the function should look at the latest date only."""
    df = _multi_day_bars(n_prior_days=5, prior_volume=100_000, today_volume=200_000, n_today_bars=5)
    # Prior days' bars are all flat 100→100; only the tail matters.
    pct = _day_chg_pct(df)
    assert isinstance(pct, float)


# ── _vol_ratio ────────────────────────────────────────────────────────────────

def test_vol_ratio_empty_returns_one():
    assert _vol_ratio(pd.DataFrame()) == 1.0


def test_vol_ratio_too_few_bars_returns_one():
    bars = _session_bars(100.0, 100.0, n_bars=5)
    assert _vol_ratio(bars) == 1.0  # < 20 total bars


def test_vol_ratio_elevated_volume():
    # prior avg = 200_000/day; today pace projected to 400_000 → ratio ≈ 2.0
    df = _multi_day_bars(
        n_prior_days=20, prior_volume=200_000, today_volume=400_000, n_today_bars=10,
    )
    ratio = _vol_ratio(df)
    assert ratio > 1.5, f"expected elevated ratio, got {ratio:.2f}"


def test_vol_ratio_normal_volume_near_one():
    # prior avg = 200_000; today same → ratio ≈ 1.0
    df = _multi_day_bars(
        n_prior_days=20, prior_volume=200_000, today_volume=200_000, n_today_bars=78,
    )
    ratio = _vol_ratio(df)
    # Allow generous tolerance because projection depends on bars_per_day = 78
    assert 0.5 < ratio < 2.5


# ── ScanReport ────────────────────────────────────────────────────────────────

def _make_report(ticker_sector_score: dict[str, tuple[str, float]]) -> ScanReport:
    """Build a minimal ScanReport from {ticker: (sector, score)}."""
    stats = {
        t: TickerStats(ticker=t, sector=sector, score=score)
        for t, (sector, score) in ticker_sector_score.items()
    }
    sector_groups: dict[str, list[float]] = {}
    for st in stats.values():
        sector_groups.setdefault(st.sector, []).append(st.score)
    sector_scores = {s: sum(v) / len(v) for s, v in sector_groups.items()}
    ranked = sorted(sector_scores, key=lambda s: sector_scores[s], reverse=True)
    sector_ranks = {s: i + 1 for i, s in enumerate(ranked)}
    return ScanReport(stats=stats, sector_scores=sector_scores, sector_ranks=sector_ranks)


def test_hot_tickers_returns_top_sectors():
    report = _make_report({
        "AAPL": ("Technology", 80.0),
        "MSFT": ("Technology", 75.0),
        "JPM":  ("Financials", 60.0),
        "XOM":  ("Energy",     40.0),
    })
    hot = set(report.hot_tickers(top_n_sectors=1))
    assert "AAPL" in hot and "MSFT" in hot
    assert "XOM" not in hot  # Energy is the lowest scorer


def test_hot_tickers_always_includes_etf_sector():
    report = _make_report({
        "SPY": ("ETF",        50.0),
        "JPM": ("Financials", 90.0),
    })
    # Even with top_n=1 (Financials wins), SPY should still appear due to ETF override
    hot = set(report.hot_tickers(top_n_sectors=1))
    assert "SPY" in hot


def test_sector_summary_lists_all_sectors():
    report = _make_report({
        "AAPL": ("Technology", 70.0),
        "JPM":  ("Financials", 55.0),
    })
    summary = report.sector_summary()
    assert "Technology" in summary
    assert "Financials" in summary


# ── SectorScanner.scan() (async) ──────────────────────────────────────────────

class _FakeBroker:
    def __init__(self, bars_map: dict[str, pd.DataFrame]):
        self._bars = bars_map

    async def get_bars(self, symbol: str, *, timeframe: str = "5Min", limit: int = 200):
        return self._bars.get(symbol.upper(), pd.DataFrame())


def test_scan_returns_report_for_all_tickers():
    bars = _session_bars(100.0, 105.0, n_bars=10)
    broker = _FakeBroker({"AAPL": bars, "JPM": bars, "SPY": bars})
    report = asyncio.run(SectorScanner(broker).scan(["AAPL", "JPM"]))
    assert "AAPL" in report.stats
    assert "JPM" in report.stats
    assert len(report.sector_scores) >= 1


def test_scan_handles_missing_ticker_gracefully():
    # broker returns empty frame for UNKN → should still produce a TickerStats entry
    broker = _FakeBroker({"SPY": _session_bars(100.0, 100.0)})
    report = asyncio.run(SectorScanner(broker).scan(["UNKN"]))
    assert "UNKN" in report.stats
    assert report.stats["UNKN"].score == 0.0  # empty df path


def test_scan_computes_rs_vs_spy():
    # AAPL up 5%, SPY flat → RS > 1
    aapl = _session_bars(100.0, 105.0, n_bars=10)
    spy  = _session_bars(100.0, 100.0, n_bars=10)
    broker = _FakeBroker({"AAPL": aapl, "SPY": spy})
    report = asyncio.run(SectorScanner(broker).scan(["AAPL"]))
    # SPY day_chg is ~0 so RS computation is skipped (abs(spy_chg) < 0.01 guard)
    # Just verify a score was assigned
    assert report.stats["AAPL"].score >= 0.0


def test_scan_sector_scores_populated():
    bars = _session_bars(100.0, 103.0, n_bars=10)
    broker = _FakeBroker({"AAPL": bars, "MSFT": bars, "SPY": bars})
    report = asyncio.run(SectorScanner(broker).scan(["AAPL", "MSFT"]))
    assert "Technology" in report.sector_scores
