# 2026-06-23 — Test-coverage hardening + dashboard squeeze bug fix

Open-ended "improve the apps" session. Focus: shore up the safety net on
previously-untested critical logic, and fix real bugs found along the way.
All changes verified by the test suites before each push.

## Baseline
- Python: 134 passed, 1 skipped → **217 passed, 1 skipped** (+83 tests)
- Dashboard (vitest): 40 passed → **56 passed** (+16 tests)
- TypeScript `tsc --noEmit`: clean

## Bug fixed
- **dashboard squeeze short-ratio shown 100× too high** (`lib/explainAgent.ts`).
  The bot emits `short_ratio` pre-formatted as a percent (Python `f"{x:.2%}"`,
  e.g. `short_ratio=70.00%`), but `humanizeSqueeze` parsed `70.00` and multiplied
  by 100 again → "7000.0%". Also fixed the `short_pressure` / `*_high_short`
  setup-tag patterns that never matched the bot's underscore forms.
  Regression test added (`tests/lib/explainAgent.test.ts`) pinning the squeeze
  fix plus technical/fundamental/risk/macro humanizers against the *exact*
  rationale strings the agents emit (a bot↔dashboard contract).

## Tests added (no behaviour change)
Python:
- `test_weight_tuner.py` — online learning loop: accuracy multipliers, weight
  renormalisation, win-rate threshold nudges + clamps, bias, api-shape entry point
- `test_squeeze_agent.py` — setup classification, rel-vol confirmation cap, backtest no-lookahead
- `test_trade_stats.py` — history summary, 20pt bias gate, loss streak, by-ticker
- `test_macro_agent.py` — `_pct_return` edges, factor scoring, inverse safe-haven
- `test_fundamental_agent.py` — keyword fallback, phrase double-weight, freshness filter
- `test_liquid_agent.py` — equity-flow directional reads, guards, invariants
- `test_insider_agent.py` — congressional scoring + technical-confirmation gate
- `test_win_rate_fills.py` — FIFO win-rate (longs, shorts, scale-in, flips)
- `test_kelly_qty.py` — sizing fail-closed guards, exposure cap, conviction monotonicity
- `test_circuit_breaker.py` — consecutive-loss + daily-loss halts (the safety brake)

Dashboard:
- `tests/lib/explainAgent.test.ts`, `tests/lib/utils.test.ts`

api_server tests are guarded with `pytest.importorskip("fastapi")` so minimal
environments skip them cleanly.

## ⚠️ Flagged follow-up (NOT fixed — needs review)
**Systemic read-modify-write race on `data/trades.json`.** Every writer
(`_check_and_close_trades`, `_trailing_stop_loop`, `_eod_position_review_loop`,
the `/api/execute` endpoint) does `trades = _load(TRADES_FILE)` *outside*
`_trades_lock`, mutates a stale snapshot, then `async with _trades_lock:
_save(...)`. These run as separate concurrent asyncio tasks, so the last save
clobbers the others — which can **drop a newly-opened trade** (→ untracked open
position) or **resurrect a closed one**. The lock only serialises the write, not
the read-modify-write as a unit.

Recommended fix: move the `_load` inside the lock and merge by a stable trade id
(reload under lock, apply this task's changes by `order_id`, then save), at all
four sites. Left unfixed because it's a cross-cutting rewrite of the money-path
persistence layer that can't be integration-tested in this environment without
risking the very data loss it aims to prevent.
