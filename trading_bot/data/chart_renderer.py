"""Chart renderer — draws a candlestick chart PNG for the VisionAgent."""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def render_chart(ticker: str, bars: Optional[pd.DataFrame]) -> Optional[str]:
    """Render a candlestick chart to a temp PNG and return the file path.

    Returns None if bars is empty or matplotlib is not available.
    The caller is responsible for deleting the file after use.
    """
    if bars is None or bars.empty or len(bars) < 10:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")   # non-interactive backend
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.lines import Line2D
    except ImportError:
        logger.debug("matplotlib not available — chart rendering disabled")
        return None

    try:
        df = bars.tail(80).copy()
        df = df.reset_index()
        xs = range(len(df))

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(10, 6), gridspec_kw={"height_ratios": [3, 1]},
            facecolor="#0a0f1e"
        )
        fig.suptitle(ticker, color="#e2e8f0", fontsize=14, fontweight="bold")

        # ── Candlesticks ──────────────────────────────────────────────────────
        for i, row in enumerate(df.itertuples()):
            color = "#22c55e" if row.close >= row.open else "#ef4444"
            ax1.plot([i, i], [row.low, row.high], color=color, linewidth=0.8)
            ax1.add_patch(mpatches.Rectangle(
                (i - 0.3, min(row.open, row.close)),
                0.6, abs(row.close - row.open),
                color=color, linewidth=0,
            ))

        # ── VWAP ──────────────────────────────────────────────────────────────
        typical = (df["high"] + df["low"] + df["close"]) / 3
        vwap    = (typical * df["volume"]).cumsum() / df["volume"].cumsum()
        ax1.plot(xs, vwap, color="#06b6d4", linewidth=1.2, label="VWAP", alpha=0.8)

        # ── EMA 9 / 21 ────────────────────────────────────────────────────────
        ema9  = df["close"].ewm(span=9,  adjust=False).mean()
        ema21 = df["close"].ewm(span=21, adjust=False).mean()
        ax1.plot(xs, ema9,  color="#f59e0b", linewidth=0.9, alpha=0.7, label="EMA9")
        ax1.plot(xs, ema21, color="#8b5cf6", linewidth=0.9, alpha=0.7, label="EMA21")

        ax1.set_facecolor("#0a0f1e")
        ax1.tick_params(colors="#64748b", labelsize=7)
        ax1.spines[:].set_color("#1e293b")
        ax1.legend(fontsize=7, loc="upper left", facecolor="#0f172a", labelcolor="#94a3b8")

        # ── Volume bars ───────────────────────────────────────────────────────
        vol_colors = ["#22c55e" if row.close >= row.open else "#ef4444"
                      for row in df.itertuples()]
        ax2.bar(xs, df["volume"], color=vol_colors, alpha=0.6, width=0.8)
        ax2.set_facecolor("#0a0f1e")
        ax2.tick_params(colors="#64748b", labelsize=6)
        ax2.spines[:].set_color("#1e293b")

        plt.tight_layout()

        # Save to temp file
        tmp = tempfile.NamedTemporaryFile(
            suffix=f"_{ticker}.png", delete=False, dir=tempfile.gettempdir()
        )
        plt.savefig(tmp.name, dpi=100, bbox_inches="tight", facecolor="#0a0f1e")
        plt.close(fig)
        return tmp.name

    except Exception as exc:
        logger.warning("chart rendering failed for %s: %s", ticker, exc)
        return None
