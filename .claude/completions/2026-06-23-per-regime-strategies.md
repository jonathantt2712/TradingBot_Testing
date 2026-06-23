# 2026-06-23 — Per-regime strategies (VIX-reconstructed) + live application

Operator ask: "identify different strategies per regime and keep that data,"
window "until regime change." Chosen: reconstruct the REAL VIX regime, and tune
**per-regime agent weights** (not just thresholds). Delivered in two increments;
both fall back to today's exact behaviour when a regime is under-sampled
(money-path safe).

## Increment 1 — regime reconstruction in the backtest (committed earlier)
- `agents/regime_agent.py`: extracted the live decision into a pure
  `classify_regime()`; `detect_regime()` calls it (behaviour-preserving) so live
  and backtest share ONE rule.
- `backtest_intraday.py`: `fetch_vix_daily()` (Yahoo ^VIX) + point-in-time
  `regime_at()` (prior-day VIX + session VWAP/day-change up to entry, no
  look-ahead) → tags each `TradeResult` with risk_on/neutral/risk_off; `run()`
  fetches QQQ + VIX and threads them in; `calc_summary` gains a `by_regime`
  breakdown.

## Increment 2 — learned per-regime strategy, applied live
- **`core/weight_tuner.py`**: factored the per-window math into a pure
  `_tune_window()`, reused for the global block AND one block per regime. `_run`
  now groups resolved trades by their `regime` tag and writes
  `regime_params: {risk_on:{...}, neutral:{...}, risk_off:{...}}` (each with its
  own learned `agent_weights`, multipliers, and LONG/SHORT thresholds).
  - A regime is only (re)tuned once it has `_MIN_TRADES` (10) of its OWN trades;
    otherwise its last good block is **kept** (the "keep that data" part) or it
    stays absent → live fallback. `update_from_trades` now carries each trade's
    regime.
- **`execution/portfolio_manager.py`**: `_tuned()` caches the whole file;
  `_regime_block()` returns the CURRENT regime's learned block (or {}).
  - `_live_weight` prefers the regime's learned weight → global tuned → settings.
  - `_composite` skips the hardcoded `_REGIME_MULTIPLIERS` when a learned block is
    active (no double-counting); uses them only as the fallback.
  - `_effective_thresholds` prefers the regime's learned thresholds → global →
    settings.
  - Returns {} when no regime is injected → **backtests/optimizer untouched**.

The strategy loop already drives the tuner from live closed trades (which carry
`regime`), so per-regime params accumulate over time automatically.

## Honest caveat (sample size)
Splitting trades by regime means each regime needs its own ≥10 trades before it
gets a distinct strategy. Until that accumulates, the bot behaves exactly as
before (global tuned params + the hardcoded regime heuristics). So this is safe
and additive, but it won't visibly change behaviour until enough per-regime live
trades exist. This is also why I did NOT force per-regime walk-forward tuning in
the grid optimizer (3 regimes × 70/30 split rarely clears the trade minimums on
free IEX history) — the online tuner accumulating live results is the better fit
for "keep that data."

## Verify
- `tests/test_regime_reconstruction.py` (7) + `tests/test_regime_strategies.py`
  (6): classify rule, no-look-ahead VIX/regime, by_regime breakdown, per-regime
  tuning + under-sampled skip, and PM apply/fallback (regime block, global,
  settings, backtest-mode ignores learned params).
- Existing weight_tuner/portfolio_manager tests still green (refactor
  behaviour-preserving). Full suite: **276 passed, 1 skipped**.

## Possible next (not done)
Grid-optimizer per-regime thresholds/ATR (segment the backtest by reconstructed
regime and tune each) — deferred for the sample-size reason above.
