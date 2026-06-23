"""Orchestrator for the validation gauntlet (Pillars 1–3).

    python -m validation.run --mode trades      # realised data/trades.json
    python -m validation.run --mode backtest     # last backtest_results.json
    python -m validation.run --mode both

`trades`   → significance test + equity/underwater on the REAL track record.
`backtest` → same on the last `python backtest_intraday.py` run (richer sample),
             plus candlestick+marker charts per ticker when bars are fetchable.

Charts need matplotlib + mplfinance (validation/requirements.txt); the stats run
on numpy/pandas alone. Outputs land in validation/out/.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from validation import metrics as M
from validation import trade_history as TH
from validation.permutation import returns_randomization_test

_HERE = Path(__file__).resolve().parent
_OUT = _HERE / "out"
_RESULTS = _HERE.parent / "backtest_results.json"


def _verdict(p: float | None) -> str:
    if p is None:
        return "—"
    return "EDGE (p<1%)" if p < 0.01 else ("weak (p<5%)" if p < 0.05 else "NOT distinguishable from luck")


def _try_plot_equity(equity, dd, name: str) -> None:
    try:
        from validation.plots import plot_equity_and_drawdown
        _OUT.mkdir(exist_ok=True)
        path = plot_equity_and_drawdown(equity, dd, _OUT / f"{name}_equity.png", title=name)
        print(f"  chart  → {path}")
    except ImportError as exc:
        print(f"  (charts skipped: {exc})")


def run_trades() -> None:
    print("\n=== Mode: REALISED trades (data/trades.json) ===")
    res = TH.analyze()
    if res.get("trades", 0) == 0:
        print("  no closed trades found — run the bot (or use --mode backtest).")
        return
    rt = res["randomization_test"]
    print(f"  trades={res['trades']}  win_rate={res['win_rate']}%  PF={res['profit_factor']}  "
          f"per-trade Sharpe={res['per_trade_sharpe']}  maxDD={res['max_drawdown']:.1%}")
    print(f"  sign-flip randomization: real={rt['real_stat']}  p={rt['p_value']}  → {_verdict(rt['p_value'])}")
    if res.get("sample_warning"):
        print(f"  ⚠️  {res['sample_warning']}")
    _try_plot_equity(res["equity"], res["drawdown"], "trades")


def _backtest_trades() -> list[dict]:
    try:
        data = json.loads(_RESULTS.read_text())
        return data.get("trades", []) if isinstance(data, dict) else []
    except Exception:
        return []


def run_backtest() -> None:
    print("\n=== Mode: BACKTEST (backtest_results.json) ===")
    trades = _backtest_trades()
    if not trades:
        print(f"  no results at {_RESULTS} — run `python backtest_intraday.py` first.")
        return
    # Trade-level returns from the backtest's recorded pnl_pct.
    r = pd.Series([float(t.get("pnl_pct") or 0) / 100.0 for t in trades], name="ret")
    eq = M.equity_curve(r)
    rand = returns_randomization_test(
        r, n=1000, seed=7,
        stat=lambda x: float(x.mean() / x.std()) if x.std() > 0 else 0.0)
    print(f"  trades={len(r)}  total={M.total_return(r):.2%}  PF={M.profit_factor(r):.2f}  "
          f"maxDD={M.max_drawdown(eq):.1%}")
    print(f"  sign-flip randomization: real={rand['real_stat']}  p={rand['p_value']}  → {_verdict(rand['p_value'])}")
    if len(r) < 100:
        print(f"  ⚠️  {len(r)} trades — protocol wants >=100 distinct days for a reliable read.")
    _try_plot_equity(eq.tolist(), M.drawdown(eq).tolist(), "backtest")
    print("  (candlestick+marker charts: see validation/plots.plot_trades_on_candles — "
          "pass a ticker's OHLC bars + its trades.)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Strategy validation gauntlet")
    ap.add_argument("--mode", choices=["trades", "backtest", "both"], default="both")
    args = ap.parse_args()
    if args.mode in ("trades", "both"):
        run_trades()
    if args.mode in ("backtest", "both"):
        run_backtest()
    print("\nNote: the strong in-sample/walk-forward PRICE-permutation tests "
          "(validation.permutation) apply to a VECTORISED signal. Re-running the "
          "async multi-agent pipeline 1000× is impractical, so the realised-returns "
          "randomization above is the feasible significance screen for this bot.\n")


if __name__ == "__main__":
    main()
