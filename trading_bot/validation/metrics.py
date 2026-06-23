"""Performance metrics on a BAR-BY-BAR strategy return series (Pillar 3).

The protocol's core unit is the bar return = position held into the bar ×
that bar's close-to-close return, with the position shifted one bar so a signal
formed on bar t only earns bar t+1's move (no look-ahead). Everything else
(equity, drawdown, Sharpe, profit factor) is derived from that series.

Pure numpy/pandas — no plotting, no IO — so it is unit-testable offline.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def bar_returns(position: pd.Series, close: pd.Series) -> pd.Series:
    """Strategy return per bar: position.shift(1) × close.pct_change().

    Shifting the position is the whole point — you cannot earn the return of the
    bar on which you decided to enter. Flat (0) bars contribute 0.
    """
    mkt = close.pct_change().fillna(0.0)
    pos = position.reindex(close.index).shift(1).fillna(0.0)
    return (pos * mkt).rename("strategy_return")


def equity_curve(returns: pd.Series, start: float = 1.0) -> pd.Series:
    return (start * (1.0 + returns.fillna(0.0)).cumprod()).rename("equity")


def drawdown(equity: pd.Series) -> pd.Series:
    peak = equity.cummax()
    return ((equity - peak) / peak).rename("drawdown")


def max_drawdown(equity: pd.Series) -> float:
    return float(drawdown(equity).min()) if len(equity) else 0.0


def sharpe(returns: pd.Series, periods: int = TRADING_DAYS) -> float:
    r = returns.dropna()
    sd = float(r.std())
    return float(r.mean() / sd * np.sqrt(periods)) if sd > 0 else 0.0


def profit_factor(returns: pd.Series) -> float:
    r = returns.dropna()
    gains = float(r[r > 0].sum())
    losses = float(-r[r < 0].sum())
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def total_return(returns: pd.Series) -> float:
    return float((1.0 + returns.fillna(0.0)).prod() - 1.0)


def win_rate(returns: pd.Series) -> float:
    r = returns[returns != 0].dropna()
    return float((r > 0).mean() * 100) if len(r) else 0.0


def summarize(returns: pd.Series, periods: int = TRADING_DAYS) -> dict:
    eq = equity_curve(returns)
    return {
        "n_bars":        int(returns.notna().sum()),
        "total_return":  round(total_return(returns), 4),
        "sharpe":        round(sharpe(returns, periods), 3),
        "profit_factor": round(profit_factor(returns), 3),
        "max_drawdown":  round(max_drawdown(eq), 4),
        "win_rate":      round(win_rate(returns), 2),
    }
