# 2026-06-23 — Autonomous paper executor on Railway

Request: "when the market opens, trade automatically in Alpaca paper." Finding:
the Vercel+Railway setup could only *recommend* + manage exits — entries required
a human click or `live_runner.py` on a PC (the `auto_execute` toggle is read only
by the PC bot). Operator chose to **build a Railway-side auto-executor** so the
existing setup truly auto-trades paper.

## What was added (`trading_bot/api_server.py`)
- **Refactor (no behaviour change):** `/api/execute` split into two reused pieces —
  `_entry_guard_reason(...)` (circuit-breaker / max-positions / sector / beta gates)
  and `_record_executed_trade(body)` (atomic guard+append+context under the lock).
  The endpoint is now a thin wrapper. This keeps the risk gates in ONE place so
  manual and auto entries can't diverge. Existing execute/circuit-breaker tests
  still pass unchanged.
- **`_auto_execute_loop()`** — new background loop (registered/cancelled in
  `lifespan`). Sweeps recommendations every `AUTO_EXEC_POLL_MIN` min during market
  hours and places Alpaca **paper** bracket orders for eligible recs, then the
  existing close/trailing/EOD loops manage exits.
- **`_auto_exec_disarmed_reason()`** — hard arming gate. Fires ONLY when ALL hold:
  `AUTO_EXECUTE_ON_RAILWAY=true`, `ALPACA_PAPER=true` (refuses a live account),
  Alpaca keys present, and the dashboard `auto_execute` toggle on. Default OFF.
- **`_auto_exec_candidates()`** — strong (LONG ≥ `AUTO_EXEC_MIN_SCORE`, SHORT ≤
  100−score), fresh (not expired), sized (qty>0). Pre-checks guards + dedups open
  name+side BEFORE placing, so a rejected entry never orphans an order.
- **`_submit_paper_bracket()`** — POSTs a market bracket order (TIF day) mirroring
  the PC broker / dashboard shape exactly.

## Safety
- Default disarmed; verified `_auto_exec_disarmed_reason()` → "AUTO_EXECUTE_ON_RAILWAY off".
- Paper-hard-gated; refuses if `ALPACA_PAPER` is false.
- Single-venue: arm Railway **or** run the PC bot — never both on one account
  (would double-trade). Documented in `.env.example` and CLAUDE.md convention.
- Reuses every existing entry guard + the circuit breaker.

## Verify
- `tests/test_auto_executor.py` — 17 tests: candidate selection (strong/weak/
  expired/unsizable), all arming gates, the shared guards (CB/positions/sector/beta),
  and an end-to-end sweep with a mocked broker (places+records, dedups, blocks
  before placing when a guard trips).
- Full suite: **253 passed, 1 skipped** (was 236). The `/api/execute` refactor is
  behaviour-preserving (existing tests green).

## Honest note on "high winrate + profit"
Not certified — and can't be. Backtests are historical estimates; the codebase
itself guards against tuning-until-it-looks-good (walk-forward in
`optimize_strategy.py`, significance test in `scorecard.py`). Edge is judged from
forward paper results over time via the existing scorecard confidence flag.

## Operator runbook (to go live for the open)
On **Railway** service env (NOT in git/chat):
1. `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET` = your Alpaca **paper** keys.
2. `ALPACA_PAPER=true`
3. `AUTO_EXECUTE_ON_RAILWAY=true`  (optional: `AUTO_EXEC_MIN_SCORE`, `AUTO_EXEC_POLL_MIN`)
4. Redeploy.
On the **dashboard**: flip the **Auto-execute** toggle ON (Trades page).
5. Confirm: make sure **no PC bot** is running the same account.
Disarm anytime by flipping the toggle off or setting `AUTO_EXECUTE_ON_RAILWAY=false`.
Watch first orders in Alpaca paper + the dashboard Positions/History.
