# 2026-06-23 — Rename backtest engine + dynamic lookback window

Two related asks from the operator: (1) the `backtest_30day.py` name was
misleading (window is configurable; the optimizer runs it at 60d), and (2) the
60-day optimizer window should be **dynamic** — "each case on its own merits,
smart logic." Applied to both tools.

## Rename
`backtest_30day.py` → `backtest_intraday.py` (window-agnostic; doesn't clash with
`backtest_runner.py`). Updated the only importers: `optimize_strategy.py` and the
`api_server.py` subprocess path + log strings. Docstring/argparse text de-30'd.

## Dynamic window — "smart, case-by-case"
The principled meaning of dynamic isn't "pick a number from volatility" — it's
**size the window to statistical sufficiency**. New pure helper in
`backtest_intraday.py`:

- `choose_window_days(trades_in_full, full_days, *, floor, cap, min_is, min_oos,
  split_frac, margin=1.3)` — from the trade *density* observed over the full
  fetched history, returns the smallest window in `[floor, cap]` projected to
  clear the statistical minimums. Walk-forward: BOTH the in-sample (`split_frac`)
  and OOS (`1-split_frac`) slices must clear theirs; single-run: `split_frac=1.0,
  min_oos=0`. Dense signals → short recent window; sparse → longer; no data → cap.
- `data_span_days()` / `trim_bars()` — measure actual fetched span; trim each
  series to the chosen window (dropping any left too short for a lookback).

Bounds (env-overridable): `BACKTEST_WINDOW_FLOOR=30`, `BACKTEST_WINDOW_CAP=120`,
`BACKTEST_MIN_TRADES=20`.

### Wiring
- `--days` now defaults to **`auto`** on both `backtest_intraday.py` and
  `optimize_strategy.py` (explicit `--days N` still forces a fixed window). The
  Railway auto-backtest/optimizer invoke with no `--days`, so they auto-size.
- **Optimizer**: fetch the cap window once → one baseline backtest to read trade
  density → `choose_window_days` (walk-forward minimums) → `trim_bars` → run the
  grid on the trimmed window. `days` flows into the dashboard files + results.
- **Standalone backtest**: run over the cap window, then keep the trades whose
  entry falls in the last `choose_window_days(...)` days (single-run minimums) —
  no second pass. Reported metrics reflect that recent, meaningful window.

## Why this shape
Reuses the existing walk-forward guards (`MIN_TRADES`/`MIN_OOS_TRADES`/`SPLIT_FRAC`)
rather than inventing a new heuristic, so the window choice is anchored to the
same statistics the optimizer already trusts — and can't overfit a "window
selector." The chooser is pure and fully unit-tested; data feed depth naturally
caps it (chosen ≤ actual span).

## Verify
- `tests/test_dynamic_window.py` — 10 tests: dense→floor, sparse→extend,
  very-sparse→cap, OOS-binds-harder, single-run≤walk-forward, cap<floor safety,
  bounds/int, plus `data_span_days`/`trim_bars`.
- Full suite: **263 passed, 1 skipped** (was 253). Both scripts parse + import;
  api_server import clean.

## Note
Auto-sizing adds one baseline backtest pass to the optimizer (negligible vs the
grid) and fetches up to `WINDOW_CAP` days of bars (heavier I/O; bounded by IEX
history depth). Lower `BACKTEST_WINDOW_CAP` if data/latency is tight.
