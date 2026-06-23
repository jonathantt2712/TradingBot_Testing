# Strategy validation suite

A rigorous gauntlet to answer one question about *this bot's* edge: **is it real,
or did we memorise noise?** Built to the three-pillar protocol (execution reality,
hindsight-bias control, data-mining defence).

## What's here
| file | purpose | runs offline? |
|------|---------|---------------|
| `metrics.py` | bar-by-bar returns, equity, underwater drawdown, Sharpe, profit factor | ✅ numpy/pandas |
| `permutation.py` | Monte Carlo price-permutation, returns sign-flip randomization, walk-forward windows + walk-forward permutation, pseudo p-values | ✅ |
| `trade_history.py` | load `data/trades.json` → returns + significance test | ✅ |
| `plots.py` | equity curve, underwater chart, candlesticks with entry/exit markers | needs `matplotlib`+`mplfinance` |
| `run.py` | orchestrator for `--mode trades / backtest / both` | stats yes; charts need libs |

The statistical core is covered by `tests/test_validation_suite.py`.

## Run it
```bash
pip install -r validation/requirements.txt        # for the charts (stats need only numpy/pandas)

python -m validation.run --mode trades             # the REAL track record (data/trades.json)
python backtest_intraday.py && \
python -m validation.run --mode backtest           # a fresh backtest sample
python -m validation.run --mode both
```
Outputs (charts) land in `validation/out/`.

## Honest limits (read these)
- **Sample size.** The sign-flip test needs trades. <30 is flagged, <100 is below
  the protocol's bar. A great-looking p-value on a dozen trades means nothing.
- **Price-permutation vs the agent pipeline.** The strong NeuroTrader-style test
  (`price_permutation_test`, `walk_forward_permutation_test`) re-runs a strategy
  on 1,000+ shuffled price paths. That's designed for a *vectorised rule*. Re-running
  the bot's async multi-agent pipeline that many times is impractical, so for the
  live strategy we use the **realised-returns randomization** (one run, then permute
  the outcomes). Use the price-permutation functions for vectorised rule research.
- **Permutations destroy volatility clustering** and autocorrelation, so shuffled
  paths are "easier" in some respects — treat the p-value as a screen, not proof.
- **Forward test anyway.** None of this replaces a multi-week live *demo* forward
  test before real capital (Pillar 2).
