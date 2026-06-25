"""chart_renderer: render_chart() temp-file contract.

Verifies that render_chart() creates a deletable temp file (callers are
responsible for cleanup — previously live_runner, main, and backtest_runner
all leaked it). Tests skip gracefully when matplotlib is not installed.
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from data.chart_renderer import render_chart


def _make_bars(n: int = 40) -> pd.DataFrame:
    """Minimal OHLCV DataFrame with a DatetimeIndex."""
    idx = pd.date_range("2026-01-02 09:35", periods=n, freq="5min", tz="UTC")
    rng = np.random.default_rng(42)
    close = 100.0 + rng.standard_normal(n).cumsum()
    return pd.DataFrame(
        {
            "open":   close + rng.uniform(-0.2, 0.2, n),
            "high":   close + rng.uniform(0.1, 0.5, n),
            "low":    close - rng.uniform(0.1, 0.5, n),
            "close":  close,
            "volume": rng.integers(50_000, 500_000, n).astype(float),
        },
        index=idx,
    )


# ── None / empty input ────────────────────────────────────────────────────────

def test_render_chart_none_input():
    assert render_chart("AAPL", None) is None


def test_render_chart_empty_df():
    assert render_chart("AAPL", pd.DataFrame()) is None


def test_render_chart_too_few_bars():
    # render_chart requires >= 10 bars
    bars = _make_bars(5)
    assert render_chart("AAPL", bars) is None


# ── Normal rendering (skipped when matplotlib unavailable) ────────────────────

def test_render_chart_returns_path():
    bars = _make_bars()
    path = render_chart("AAPL", bars)
    if path is None:
        pytest.skip("matplotlib not available")
    try:
        assert Path(path).exists(), "temp file must exist after render_chart()"
        assert path.endswith(".png")
    finally:
        os.unlink(path)


def test_render_chart_file_is_deletable():
    """Caller must be able to unlink the file — documents the cleanup contract."""
    bars = _make_bars()
    path = render_chart("TSLA", bars)
    if path is None:
        pytest.skip("matplotlib not available")
    assert Path(path).exists()
    os.unlink(path)
    assert not Path(path).exists()


def test_render_chart_different_tickers_different_files():
    bars = _make_bars()
    p1 = render_chart("AAPL", bars)
    p2 = render_chart("NVDA", bars)
    if p1 is None or p2 is None:
        pytest.skip("matplotlib not available")
    try:
        assert p1 != p2, "each call must create a distinct temp file"
        assert "AAPL" in p1
        assert "NVDA" in p2
    finally:
        for p in (p1, p2):
            try:
                os.unlink(p)
            except OSError:
                pass
