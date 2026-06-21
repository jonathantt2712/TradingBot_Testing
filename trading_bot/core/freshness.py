"""Bar-freshness check — fail closed on stale market data.

A decision is only as trustworthy as the price it was built on. If the most
recent bar is far older than the series' own cadence (a data-feed gap, a trading
halt, or a stale weekend snapshot), sizing and routing an order against it risks
executing at a price that no longer exists. The RiskAgent treats a stale series
as structurally unsound and vetoes the trade.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd


def bar_staleness(
    bars: Optional[pd.DataFrame],
    *,
    now: Optional[datetime] = None,
    max_age_factor: float = 3.0,
) -> tuple[bool, Optional[str]]:
    """Return ``(is_stale, reason)`` for an OHLCV series.

    The bar cadence is inferred from the median spacing of the series itself, so
    the check works for any timeframe without being told which one.
    ``max_age_factor`` is how many cadence-intervals the last bar may lag ``now``
    before the series is judged stale; pass ``<= 0`` to disable the check.
    """
    if max_age_factor <= 0:
        return False, None
    if bars is None or len(bars) < 3:
        return True, "insufficient bars to assess freshness"

    idx = bars.index
    if not isinstance(idx, pd.DatetimeIndex):
        return False, None  # non-temporal index (e.g. synthetic data) — can't judge

    # Infer cadence from the recent spacings (median is robust to the odd gap).
    deltas = pd.Series(idx[-21:]).diff().dropna()
    if deltas.empty:
        return True, "cannot infer bar cadence"
    cadence = deltas.median()
    if cadence <= pd.Timedelta(0):
        return True, "non-monotonic bar timestamps"

    last_ts = pd.Timestamp(idx[-1])
    now_ts = pd.Timestamp(now or datetime.now(timezone.utc))
    # Align tz-awareness (assume UTC when the series is tz-naive).
    if last_ts.tzinfo is None:
        last_ts = last_ts.tz_localize("UTC")
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize("UTC")

    age = now_ts - last_ts
    if age > cadence * max_age_factor:
        return True, (
            f"last bar {age} old vs {cadence} cadence "
            f"(>{max_age_factor:g}x — stale feed/halt)"
        )
    return False, None
