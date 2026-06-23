"""Visual outputs (Pillar 3): equity curve, underwater drawdown, and candlesticks
with the exact entry/exit markers for the real trades taken.

Requires matplotlib + mplfinance (see validation/requirements.txt). Imports are
deferred so importing this module never crashes a box that lacks the libs — the
helpful error only fires when you actually try to plot.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pandas as pd


def _matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")            # headless / server safe
        import matplotlib.pyplot as plt
        return plt
    except Exception as exc:  # pragma: no cover - depends on optional libs
        raise ImportError("matplotlib not installed — `pip install -r "
                          "validation/requirements.txt`") from exc


def plot_equity_and_drawdown(equity: Sequence[float], drawdown: Sequence[float],
                             out: str | Path, title: str = "Strategy") -> str:
    """Top: equity curve. Bottom: underwater (drawdown %) — the chart that tells
    the real story a smooth equity line hides."""
    plt = _matplotlib()
    fig, (ax1, ax2) = plt.subplots(
        2, 1, sharex=True, figsize=(11, 7), gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(range(len(equity)), list(equity), color="#1f77b4", lw=1.4)
    ax1.set_title(f"{title} — equity curve")
    ax1.set_ylabel("Equity (×)")
    ax1.grid(alpha=0.3)
    dd = [d * 100 for d in drawdown]
    ax2.fill_between(range(len(dd)), dd, 0, color="crimson", alpha=0.5)
    ax2.set_title("Underwater (drawdown %)")
    ax2.set_ylabel("%")
    ax2.set_xlabel("trade / bar #")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return str(out)


def plot_trades_on_candles(bars: pd.DataFrame, trades: list[dict], out: str | Path,
                           title: str = "Trades") -> str:
    """Candlestick chart with entry (^) and exit (v) markers placed at the real
    fill times/prices, so you can visually verify WHERE the algo traded.

    `bars` must be a DatetimeIndex OHLC frame (columns open/high/low/close[/volume]);
    `trades` carry entry_time/exit_time/entry/exit/direction (str timestamps ok).
    """
    try:
        import mplfinance as mpf
    except Exception as exc:  # pragma: no cover
        raise ImportError("mplfinance not installed — `pip install -r "
                          "validation/requirements.txt`") from exc

    df = bars.copy()
    df.index = pd.to_datetime(df.index)
    entry_y = pd.Series(float("nan"), index=df.index)
    exit_y = pd.Series(float("nan"), index=df.index)
    for t in trades:
        et, xt = pd.to_datetime(t.get("entry_time")), pd.to_datetime(t.get("exit_time"))
        ei = df.index.searchsorted(et)
        xi = df.index.searchsorted(xt)
        if 0 <= ei < len(df):
            entry_y.iloc[ei] = float(t.get("entry") or t.get("entry_price") or df["low"].iloc[ei])
        if 0 <= xi < len(df):
            exit_y.iloc[xi] = float(t.get("exit") or t.get("exit_price") or df["high"].iloc[xi])

    aps = []
    if entry_y.notna().any():
        aps.append(mpf.make_addplot(entry_y, type="scatter", marker="^", markersize=80, color="green"))
    if exit_y.notna().any():
        aps.append(mpf.make_addplot(exit_y, type="scatter", marker="v", markersize=80, color="red"))
    mpf.plot(df, type="candle", style="charles", addplot=aps or None,
             title=title, volume=("volume" in df.columns), savefig=dict(fname=str(out), dpi=120))
    return str(out)
