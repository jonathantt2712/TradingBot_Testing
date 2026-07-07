"""Measured execution slippage from the bot's own fills.

The close monitor records ``entry_slippage_bps`` on every real closed trade
(parent fill vs intended entry). Backtests and the optimizer cost simulated
fills with this measured number instead of a hardcoded guess, so the nightly
auto-apply optimizes against what execution ACTUALLY costs at this venue.
"""
from __future__ import annotations

import json
import statistics
from typing import Optional

from core.paths import data_dir

_MIN_SAMPLES = 10     # below this, the estimate is noise — use the default
_WINDOW      = 100    # most recent real fills considered
# Sanity band (fraction per side). Median is outlier-robust, but a broken
# recorder must never push simulations to absurd costs (or to zero).
_FLOOR, _CEIL = 0.0001, 0.005


def measured_slippage_pct(default: float) -> float:
    """Median |entry slippage| as a per-side fraction, or ``default``.

    Reads the venue's own trades.json (volume-aware via data_dir()). Falls
    back to ``default`` when there aren't enough real fills to trust.
    """
    try:
        trades = json.loads((data_dir() / "trades.json").read_text(encoding="utf-8"))
    except Exception:
        return default
    if not isinstance(trades, list):
        return default

    samples = [
        abs(float(t["entry_slippage_bps"]))
        for t in trades[-_WINDOW * 3:]          # cheap pre-slice; filter below
        if isinstance(t, dict) and t.get("entry_slippage_bps") is not None
    ][-_WINDOW:]
    if len(samples) < _MIN_SAMPLES:
        return default

    median_bps = statistics.median(samples)
    return min(max(median_bps / 10_000.0, _FLOOR), _CEIL)


def slippage_summary() -> Optional[dict]:
    """Small summary for dashboards/logs: sample count + median bps, or None."""
    try:
        trades = json.loads((data_dir() / "trades.json").read_text(encoding="utf-8"))
        samples = [
            abs(float(t["entry_slippage_bps"]))
            for t in trades
            if isinstance(t, dict) and t.get("entry_slippage_bps") is not None
        ][-_WINDOW:]
    except Exception:
        return None
    if not samples:
        return None
    return {
        "samples":    len(samples),
        "median_bps": round(statistics.median(samples), 2),
        "worst_bps":  round(max(samples), 2),
    }
