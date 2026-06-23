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


def run_proxy(bars_csv: str) -> None:
    """Strong tests on the vectorised composite proxy over a real OHLC series.

    Runs the 1,000× in-sample price-permutation and the walk-forward permutation
    (these need a cheap signal — hence the proxy), then renders equity/underwater
    and a candlestick with the proxy's entry/exit markers."""
    from validation.proxy_signal import momentum_signal, fit_momentum
    from validation.permutation import price_permutation_test, walk_forward_permutation_test

    df = pd.read_csv(bars_csv, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    close = df["close"]
    print("\n=== Mode: PROXY (vectorised composite backbone) ===")
    print(f"  bars={len(df)} from {bars_csv}")

    sig = fit_momentum(close)(close)
    rets = M.bar_returns(sig, close)
    print(f"  in-sample stats: {M.summarize(rets)}")

    n_is = 1000 if len(df) >= 200 else 200
    isp = price_permutation_test(close, lambda c: fit_momentum(close)(c), n=n_is, seed=11)
    print(f"  in-sample MC permutation (n={isp['n']}): real Sharpe={isp['real_stat']} "
          f"p={isp['p_value']} → {_verdict(isp['p_value'])}")

    train = max(60, int(len(df) * 0.6))
    if len(df) > train + 40:
        wfp = walk_forward_permutation_test(close, fit_momentum, train=train,
                                            test=max(20, (len(df) - train) // 5), n=200, seed=13)
        print(f"  walk-forward permutation (n={wfp['n']}): real Sharpe={wfp['real_stat']} "
              f"p={wfp['p_value']} → {_verdict(wfp['p_value'])}")
    else:
        print("  walk-forward permutation: not enough bars (need >~train+40).")

    try:
        from validation.plots import plot_equity_and_drawdown, plot_trades_on_candles
        _OUT.mkdir(exist_ok=True)
        eq = M.equity_curve(rets)
        print("  chart  → " + plot_equity_and_drawdown(eq.tolist(), M.drawdown(eq).tolist(),
                                                        _OUT / "proxy_equity.png", "Proxy"))
        trades = _proxy_trades(sig, df)
        print("  chart  → " + plot_trades_on_candles(df.tail(300), trades, _OUT / "proxy_candles.png",
                                                      "Proxy entries/exits"))
    except ImportError as exc:
        print(f"  (charts skipped: {exc})")


def _proxy_trades(position: pd.Series, bars: pd.DataFrame) -> list[dict]:
    """Turn a position series into (entry,exit) trades at every change of stance."""
    trades, cur = [], None
    for ts, p in position.items():
        if cur is None and p != 0:
            cur = {"entry_time": ts, "entry": float(bars.loc[ts, "close"]),
                   "direction": "LONG" if p > 0 else "SHORT"}
        elif cur is not None and p != (1.0 if cur["direction"] == "LONG" else -1.0):
            cur["exit_time"] = ts
            cur["exit"] = float(bars.loc[ts, "close"])
            trades.append(cur)
            cur = ({"entry_time": ts, "entry": float(bars.loc[ts, "close"]),
                    "direction": "LONG" if p > 0 else "SHORT"} if p != 0 else None)
    return trades


def main() -> None:
    ap = argparse.ArgumentParser(description="Strategy validation gauntlet")
    ap.add_argument("--mode", choices=["trades", "backtest", "both"], default="both")
    ap.add_argument("--bars", help="OHLC CSV (DatetimeIndex) → run the strong proxy "
                                    "permutation tests + candlestick markers")
    args = ap.parse_args()
    if args.mode in ("trades", "both"):
        run_trades()
    if args.mode in ("backtest", "both"):
        run_backtest()
    if args.bars:
        run_proxy(args.bars)
    print("\nNote: the strong in-sample/walk-forward PRICE-permutation tests run on the "
          "VECTORISED proxy (validation.proxy_signal) — re-running the async multi-agent "
          "pipeline 1000× is impractical. The realised-returns randomization is the screen "
          "for the full bot; the proxy screens its momentum backbone.\n")


if __name__ == "__main__":
    main()
