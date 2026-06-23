"""Vectorised proxy of the composite, for the HEAVY permutation tests.

The live edge is a 7-agent async composite — far too slow to re-run 1,000× on
shuffled price paths. This is a transparent, fast stand-in: a trend/momentum
signal with the same long/short shape, so `price_permutation_test` and
`walk_forward_permutation_test` can run at full count.

It approximates the PRICE-DRIVEN (technical) backbone of the composite, NOT the
whole pipeline. So its pseudo p-value is a screen on that backbone's robustness,
not a verdict on every agent. Stated plainly so nobody mistakes it for the real
thing.
"""
from __future__ import annotations

import pandas as pd

from validation.metrics import bar_returns, sharpe

# Small grid the walk-forward "fit" optimises over — deliberately tiny so the
# permutation test is challenging exactly the optimisation we'd do live.
_GRID = [(5, 20), (10, 30), (10, 50), (20, 60), (20, 100)]


def momentum_signal(close: pd.Series, fast: int = 10, slow: int = 30) -> pd.Series:
    """+1 long when the fast MA is above the slow MA, −1 short when below."""
    f = close.rolling(fast).mean()
    s = close.rolling(slow).mean()
    pos = pd.Series(0.0, index=close.index)
    pos[f > s] = 1.0
    pos[f < s] = -1.0
    return pos.fillna(0.0)


def fit_momentum(train_close: pd.Series, grid=_GRID):
    """Optimise (fast, slow) by in-sample Sharpe; return a signal_fn bound to the
    winner. This is the per-window 'optimisation' the walk-forward permutation
    test then challenges on unseen / shuffled data."""
    best, best_s = grid[0], -1e9
    for fast, slow in grid:
        s = sharpe(bar_returns(momentum_signal(train_close, fast, slow), train_close))
        if s > best_s:
            best_s, best = s, (fast, slow)
    fast, slow = best
    return lambda c: momentum_signal(c, fast, slow)
