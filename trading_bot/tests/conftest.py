"""Shared fixtures — make trading_bot importable and provide bar builders."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def make_session_bars(
    closes,
    *,
    bar_range: float = 1.0,
    volume: int = 10_000,
    start: str = "2026-06-09 13:30:00",  # 09:30 ET in UTC
):
    """Build a single-session 5-min OHLCV frame with a UTC DatetimeIndex.

    Each bar's high/low straddle the close by bar_range/2 so ATR is ~bar_range.
    """
    closes = list(closes)
    idx = pd.date_range(start=start, periods=len(closes), freq="5min", tz=timezone.utc)
    closes_arr = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes_arr[0]], closes_arr[:-1]])
    half = bar_range / 2.0
    return pd.DataFrame(
        {
            "open":   opens,
            "high":   np.maximum(opens, closes_arr) + half,
            "low":    np.minimum(opens, closes_arr) - half,
            "close":  closes_arr,
            "volume": [volume] * len(closes),
        },
        index=idx,
    )


@pytest.fixture
def flat_bars():
    """40 bars hovering around 100 with ~1.0 ATR."""
    return make_session_bars([100.0] * 40)
