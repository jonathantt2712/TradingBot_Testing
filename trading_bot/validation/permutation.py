"""Monte Carlo permutation & walk-forward tests (Pillar 3).

The question every test here answers: is the measured edge distinguishable from
luck, or did we just memorise noise? Two flavours:

1. price_permutation_test — the strong, NeuroTrader-style in-sample test: shuffle
   the price returns, REBUILD a price path, and RE-RUN the strategy on it 1000×.
   Real optimisation should beat the noise distribution (pseudo p-value < 1%).
   Requires a CHEAP vectorised `signal_fn(close)->position`. The live bot's async
   multi-agent pipeline is far too slow to run 1000× — see README; for that, use
   the returns_randomization_test on a single realised run instead.

2. returns_randomization_test — feasible for ANY realised return stream (live
   trades or one backtest) WITHOUT re-running the strategy: randomly flip the
   sign of each bar/trade return under the null "returns are symmetric about 0
   (no edge)" and see how often noise matches the real statistic.

Caveat baked in by design (per the protocol): shuffling returns destroys
volatility clustering and autocorrelation, so permuted paths are "easier" in some
respects — treat the p-value as a screen, not proof.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pandas as pd

from validation.metrics import bar_returns, sharpe


def permute_close(close: pd.Series, seed: Optional[int] = None) -> pd.Series:
    """Shuffle log-returns and rebuild a price path anchored at the first price.

    Preserves the return distribution (drift + volatility) but destroys order,
    autocorrelation and volatility clustering."""
    rng = np.random.default_rng(seed)
    logr = np.log(close / close.shift(1)).dropna().to_numpy()
    perm = rng.permutation(logr)
    path = float(close.iloc[0]) * np.exp(np.concatenate([[0.0], np.cumsum(perm)]))
    return pd.Series(path, index=close.index, name=close.name)


def pseudo_p_value(real_stat: float, perm_stats) -> float:
    """One-sided pseudo p-value P(noise >= real), with +1 smoothing so it is
    never exactly 0 (you cannot prove p=0 from a finite sample)."""
    perm = np.asarray(list(perm_stats), dtype=float)
    perm = perm[~np.isnan(perm)]
    if perm.size == 0:
        return 1.0
    return float((np.sum(perm >= real_stat) + 1) / (perm.size + 1))


def returns_randomization_test(
    returns,
    n: int = 1000,
    stat: Optional[Callable[[np.ndarray], float]] = None,
    seed: Optional[int] = None,
) -> dict:
    """Sign-flip randomization on a realised return series.

    Null: each return is equally likely to have been + or − (no directional
    edge). p = fraction of sign-flipped universes whose statistic >= the real."""
    r = np.asarray(list(returns), dtype=float)
    r = r[~np.isnan(r)]
    if stat is None:
        def stat(x):  # annualised-ish Sharpe of the stream
            sd = x.std()
            return float(x.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0
    real = stat(r)
    rng = np.random.default_rng(seed)
    perm = [stat(r * rng.choice((-1.0, 1.0), size=r.size)) for _ in range(n)]
    return {
        "real_stat":  round(real, 4),
        "p_value":    round(pseudo_p_value(real, perm), 4),
        "n":          n,
        "perm_mean":  round(float(np.mean(perm)), 4),
        "perm_std":   round(float(np.std(perm)), 4),
        "significant": pseudo_p_value(real, perm) < 0.01,
    }


def price_permutation_test(
    close: pd.Series,
    signal_fn: Callable[[pd.Series], pd.Series],
    n: int = 1000,
    metric: Optional[Callable[[pd.Series], float]] = None,
    seed: Optional[int] = None,
) -> dict:
    """In-sample MC permutation for a VECTORISED rule strategy.

    Runs `signal_fn` on the real series and on `n` return-shuffled paths; the real
    metric should sit far in the right tail (p < 1%) if the edge is real."""
    metric = metric or sharpe
    real = metric(bar_returns(signal_fn(close), close))
    perm_stats = []
    for i in range(n):
        s = None if seed is None else seed + i + 1
        pc = permute_close(close, seed=s)
        perm_stats.append(metric(bar_returns(signal_fn(pc), pc)))
    p = pseudo_p_value(real, perm_stats)
    return {
        "real_stat":   round(float(real), 4),
        "p_value":     round(p, 4),
        "n":           n,
        "perm_mean":   round(float(np.nanmean(perm_stats)), 4),
        "significant": p < 0.01,
    }


def walk_forward_windows(n: int, train: int, test: int, step: Optional[int] = None):
    """(train_slice, test_slice) pairs over an index of length n.

    e.g. train≈4y of bars, test≈30 bars, step=test → non-overlapping OOS windows
    rolled forward. The concatenation of all test slices is the honest OOS curve.
    """
    step = step or test
    out = []
    i = train
    while i + test <= n:
        out.append((slice(i - train, i), slice(i, i + test)))
        i += step
    return out


def walk_forward_oos_returns(
    close: pd.Series,
    fit_fn: Callable[[pd.Series], Callable[[pd.Series], pd.Series]],
    train: int,
    test: int,
    step: Optional[int] = None,
) -> pd.Series:
    """Stitch out-of-sample bar returns across rolling windows.

    `fit_fn(train_close)` returns a fitted `signal_fn` which is then applied ONLY
    to the following (unseen) test window — that is what makes it walk-forward."""
    pieces = []
    for tr, te in walk_forward_windows(len(close), train, test, step):
        signal_fn = fit_fn(close.iloc[tr])
        seg = close.iloc[te]
        pieces.append(bar_returns(signal_fn(seg), seg))
    return pd.concat(pieces) if pieces else pd.Series(dtype=float, name="strategy_return")


def walk_forward_permutation_test(
    close: pd.Series,
    fit_fn,
    train: int,
    test: int,
    n: int = 200,
    step: Optional[int] = None,
    metric: Optional[Callable[[pd.Series], float]] = None,
    seed: Optional[int] = None,
) -> dict:
    """Permute the FUTURE (post-train) data and re-run the walk-forward.

    Tests whether the OOS edge survives when only the unseen data is shuffled —
    the hardest screen against selection/optimisation bias."""
    metric = metric or sharpe
    real = metric(walk_forward_oos_returns(close, fit_fn, train, test, step))
    perm_stats = []
    for i in range(n):
        s = None if seed is None else seed + i + 1
        head = close.iloc[:train]
        future_perm = permute_close(close.iloc[train - 1:], seed=s).iloc[1:]
        permuted = pd.concat([head, future_perm])
        perm_stats.append(metric(walk_forward_oos_returns(permuted, fit_fn, train, test, step)))
    p = pseudo_p_value(real, perm_stats)
    return {"real_stat": round(float(real), 4), "p_value": round(p, 4),
            "n": n, "significant": p < 0.01}
