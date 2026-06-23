"""Run the gauntlet on REALISED trades (data/trades.json) — the honest record.

Live/paper trades give us per-trade returns but not a dense bar series, so the
significance screen here is the trade-level returns_randomization_test (no
strategy re-run needed). This is the "both" half that works on the actual
track record rather than a backtest.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from validation.metrics import equity_curve, drawdown, max_drawdown, profit_factor
from validation.permutation import returns_randomization_test

_TRADES_FILE = Path(__file__).resolve().parents[1] / "data" / "trades.json"


def load_closed_trades(path: Path | None = None) -> list[dict]:
    p = path or _TRADES_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [t for t in data if t.get("status") == "closed" and t.get("pnl") is not None]


def trade_returns(trades: list[dict]) -> pd.Series:
    """Per-trade fractional return, signed by outcome.

    Prefer the recorded pnl_pct; else derive from entry/exit and direction."""
    rows = []
    for t in sorted(trades, key=lambda x: x.get("closed_at") or x.get("executed_at") or ""):
        pct = t.get("pnl_pct")
        if pct is None:
            entry = float(t.get("entry") or 0)
            exit_ = float(t.get("exit") or 0)
            if entry <= 0 or exit_ <= 0:
                continue
            d = 1.0 if str(t.get("direction", "LONG")).upper() == "LONG" else -1.0
            pct = d * (exit_ - entry) / entry * 100.0
        rows.append(float(pct) / 100.0)
    return pd.Series(rows, name="trade_return")


def analyze(path: Path | None = None, n_perm: int = 1000, seed: int | None = 7) -> dict:
    """Summary + equity/drawdown + a sign-flip significance test on the realised
    trade returns. Returns enough for plotting and a verdict."""
    trades = load_closed_trades(path)
    r = trade_returns(trades)
    if r.empty:
        return {"trades": 0, "message": "no closed trades to analyse"}

    eq = equity_curve(r)
    # per-trade Sharpe (NOT annualised — trade cadence is irregular)
    per_trade_sharpe = float(r.mean() / r.std()) if r.std() > 0 else 0.0
    rand = returns_randomization_test(
        r, n=n_perm, seed=seed,
        stat=lambda x: float(x.mean() / x.std()) if x.std() > 0 else 0.0,
    )
    return {
        "trades":            int(len(r)),
        "total_return":      round(float((1 + r).prod() - 1), 4),
        "win_rate":          round(float((r > 0).mean() * 100), 2),
        "profit_factor":     round(profit_factor(r), 3),
        "per_trade_sharpe":  round(per_trade_sharpe, 3),
        "max_drawdown":      round(max_drawdown(eq), 4),
        "randomization_test": rand,
        "equity":            eq.tolist(),
        "drawdown":          drawdown(eq).tolist(),
        # Honest sample-size flag — a sign-flip p-value on <30 trades is fragile.
        "sample_warning": ("Sample too small for a trustworthy edge test "
                           f"({len(r)} trades; want >=30, ideally >=100)."
                           if len(r) < 30 else None),
    }
