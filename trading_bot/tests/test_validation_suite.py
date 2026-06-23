"""Statistical core of the validation suite (Pillar 3).

Covers the parts that run offline with numpy/pandas: bar-by-bar returns, the
permutation machinery, walk-forward windowing, and the trade-history analyser.
Plotting / live-data execution are validated by hand on deploy (need viz libs +
Alpaca keys), not here.
"""
import json

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pandas")

from validation import metrics as M
from validation import permutation as P
from validation import trade_history as TH


# ── metrics ──────────────────────────────────────────────────────────────────

def test_bar_returns_shift_no_lookahead():
    close = pd.Series([100, 110, 121])           # +10%, +10%
    pos = pd.Series([1, 1, 1])                   # always long
    r = M.bar_returns(pos, close)
    # bar 0: no prior position → 0; bar 1: pos held from bar0 × +10% = 0.10
    assert r.iloc[0] == 0.0
    assert abs(r.iloc[1] - 0.10) < 1e-9


def test_equity_and_drawdown():
    r = pd.Series([0.10, -0.50, 0.0])
    eq = M.equity_curve(r)                        # 1.0 → 1.1 → 0.55 → 0.55
    assert abs(eq.iloc[-1] - 0.55) < 1e-9
    assert abs(M.max_drawdown(eq) - (-0.5)) < 1e-9


def test_profit_factor_and_sharpe_sign():
    r = pd.Series([0.02, -0.01, 0.03, -0.01])
    assert M.profit_factor(r) == pytest.approx(0.05 / 0.02)
    assert M.sharpe(r) > 0


# ── permutation primitives ────────────────────────────────────────────────────

def test_permute_close_preserves_endpoints_and_reorders():
    close = pd.Series(np.linspace(100, 150, 50))
    perm = P.permute_close(close, seed=1)
    assert perm.iloc[0] == close.iloc[0]
    assert perm.iloc[-1] == pytest.approx(close.iloc[-1])   # drift preserved
    assert not np.allclose(perm.values, close.values)        # path reordered


def test_pseudo_p_value_bounds():
    assert P.pseudo_p_value(10, [1, 2, 3]) == pytest.approx(1 / 4)   # none >= 10
    assert P.pseudo_p_value(0, [1, 2, 3]) == pytest.approx(4 / 4)    # all >= 0


def test_randomization_flags_real_edge_and_clears_noise():
    rng = np.random.default_rng(0)
    edge = pd.Series(0.01 + 0.002 * rng.standard_normal(300))   # persistently +ve
    noise = pd.Series([0.01, -0.01] * 150)                       # mean 0, no edge
    assert P.returns_randomization_test(edge, n=500, seed=1)["p_value"] < 0.05
    assert P.returns_randomization_test(noise, n=500, seed=1)["p_value"] > 0.10


def test_price_permutation_real_beats_noise_for_momentum():
    # Trending series with autocorrelation; momentum captures it, but shuffling
    # destroys the autocorrelation → real stat should top the noise mean.
    rng = np.random.default_rng(3)
    rets = 0.002 + 0.01 * rng.standard_normal(400)
    close = pd.Series(100 * np.exp(np.cumsum(rets)))

    def momentum(c):
        return (c.pct_change(5) > 0).astype(float) * 2 - 1   # +1 long / -1 short

    res = P.price_permutation_test(close, momentum, n=150, seed=5)
    assert res["real_stat"] > res["perm_mean"]


def test_walk_forward_windows_count_and_shape():
    wins = P.walk_forward_windows(100, train=40, test=20, step=20)
    assert len(wins) == 3
    tr, te = wins[0]
    assert (tr.start, tr.stop, te.start, te.stop) == (0, 40, 40, 60)


def test_walk_forward_oos_returns_length():
    close = pd.Series(np.linspace(100, 120, 100))
    fit = lambda train_c: (lambda c: pd.Series(1.0, index=c.index))  # always long
    oos = P.walk_forward_oos_returns(close, fit, train=40, test=20, step=20)
    assert len(oos) == 60   # 3 test windows × 20


# ── trade-history analyser ────────────────────────────────────────────────────

def test_trade_history_analyze(tmp_path):
    trades = [
        {"status": "closed", "pnl": 100, "pnl_pct": 2.0, "closed_at": "2026-06-01"},
        {"status": "closed", "pnl": -50, "pnl_pct": -1.0, "closed_at": "2026-06-02"},
        {"status": "closed", "pnl": 80, "pnl_pct": 1.5, "closed_at": "2026-06-03"},
        {"status": "open",   "pnl": None},
    ]
    f = tmp_path / "trades.json"
    f.write_text(json.dumps(trades))
    out = TH.analyze(f, n_perm=200, seed=1)
    assert out["trades"] == 3                       # the open one is excluded
    assert out["win_rate"] == pytest.approx(66.67, abs=0.1)
    assert "randomization_test" in out and len(out["equity"]) == 3
    assert out["sample_warning"] is not None        # <30 trades → flagged
