"""Real trade history — the data behind the dashboard's "History" tab.

Single source of truth = ``data/trades.json`` (the same file ``/api/history``
serves). The backtest and the optimizer both read this so they learn from what
actually happened in the (paper/live) account, not just simulated fills.

All readers are fail-soft: a missing/corrupt file yields an empty history and
never raises — these stats are advisory and must not break a backtest run.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_TRADES_FILE = Path(__file__).parent.parent / "data" / "trades.json"


def load_closed_trades(path: Optional[Path] = None) -> list[dict]:
    """Return the list of CLOSED trades (those with a realised P&L)."""
    p = path or _TRADES_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [t for t in data
                    if t.get("status") == "closed" and t.get("pnl") is not None]
    except Exception:
        logger.debug("trade history unavailable", exc_info=True)
    return []


def _win_rate(trades: list[dict]) -> Optional[float]:
    if not trades:
        return None
    return round(100 * len([t for t in trades if (t.get("pnl") or 0) > 0]) / len(trades), 1)


def summarize(trades: list[dict]) -> dict:
    """Summary stats over closed trades: win rate, P&L, per-direction, per-ticker."""
    n = len(trades)
    if n == 0:
        return {"closed": 0}

    pnls = [float(t.get("pnl") or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    longs  = [t for t in trades if str(t.get("direction", "")).upper() == "LONG"]
    shorts = [t for t in trades if str(t.get("direction", "")).upper() == "SHORT"]

    by_ticker: dict[str, dict] = {}
    for t in trades:
        sym = str(t.get("ticker", "")).upper()
        if not sym:
            continue
        d = by_ticker.setdefault(sym, {"trades": 0, "wins": 0, "pnl": 0.0})
        d["trades"] += 1
        d["pnl"] += float(t.get("pnl") or 0)
        if (t.get("pnl") or 0) > 0:
            d["wins"] += 1
    for d in by_ticker.values():
        d["win_rate"] = round(100 * d["wins"] / d["trades"], 1) if d["trades"] else 0.0
        d["pnl"] = round(d["pnl"], 2)

    # Most-recent consecutive losing exits (chronological order assumed in file).
    streak = 0
    for t in reversed(trades):
        if (t.get("pnl") or 0) < 0:
            streak += 1
        else:
            break

    long_wr, short_wr = _win_rate(longs), _win_rate(shorts)
    bias = "neutral"
    if long_wr is not None and short_wr is not None:
        if long_wr > short_wr + 20:
            bias = "long"
        elif short_wr > long_wr + 20:
            bias = "short"

    return {
        "closed":             n,
        "win_rate":           round(100 * len(wins) / n, 1),
        "total_pnl":          round(sum(pnls), 2),
        "avg_pnl":            round(sum(pnls) / n, 2),
        "long_trades":        len(longs),
        "short_trades":       len(shorts),
        "long_win_rate":      long_wr,
        "short_win_rate":     short_wr,
        "bias":               bias,
        "recent_loss_streak": streak,
        "by_ticker":          by_ticker,
    }


def format_block(stats: dict, *, title: str = "LIVE TRADE HISTORY (history tab)") -> str:
    """One compact, log-friendly block summarising the real history."""
    if not stats or stats.get("closed", 0) == 0:
        return f"{title}: no closed trades yet — nothing to learn from."
    lines = [
        f"{title}: {stats['closed']} closed | win {stats['win_rate']}% | "
        f"P&L ${stats['total_pnl']:+.2f} | avg ${stats['avg_pnl']:+.2f}",
        f"  direction: LONG {stats['long_win_rate']}% ({stats['long_trades']}) vs "
        f"SHORT {stats['short_win_rate']}% ({stats['short_trades']}) -> bias {stats['bias']}",
    ]
    if stats.get("recent_loss_streak", 0) >= 2:
        lines.append(f"  ! {stats['recent_loss_streak']} consecutive losing exits most recently")
    bt = stats.get("by_ticker", {})
    if bt:
        ranked = sorted(bt.items(), key=lambda kv: kv[1]["pnl"], reverse=True)
        best, worst = ranked[0], ranked[-1]
        lines.append(
            f"  best: {best[0]} ${best[1]['pnl']:+.2f} ({best[1]['win_rate']}%) | "
            f"worst: {worst[0]} ${worst[1]['pnl']:+.2f} ({worst[1]['win_rate']}%)"
        )
    return "\n".join(lines)
