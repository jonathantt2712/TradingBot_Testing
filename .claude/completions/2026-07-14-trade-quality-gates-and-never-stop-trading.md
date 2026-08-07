# Trade-quality gates + "never stop trading" (2026-07-14)

Branch `claude/telegram-alerts-trading-logic-one5hm`, merged with
`main` at `05b6ddb`. Suite: **478 passed, 1 skipped**.

## Shipped

| Commit | Change |
|---|---|
| bf3fabf | Trade-quality gates: spread veto, late-entry cutoff parity, optimizer-validated time-stop |
| c27583b | Never stop trading: adaptive thresholds + advisory-only circuit breaker |
| 05b6ddb | Merge of testing/main (kept `_strategy_alerted` throttle + GC-safe `_fire()`; took remote Telegram entry/exit send logging) |

## bf3fabf — three expectancy leaks

1. **Spread veto** (`MAX_SPREAD_BPS=30`). Nothing checked bid-ask spread
   anywhere; on a 2:1 R/R day trade 40bps of round-trip cost erases most of
   the edge. Enforced at scan time (recs rejected) and at entry time in
   `PortfolioManager` via a new `get_quote` on the broker interface.
   Fail-open when no quote is available.
2. **Late-entry cutoff** (`ENTRY_CUTOFF_MIN=60`). The backtest always
   refused entries after ~15:00 ET while live entered until 15:55 — live was
   taking a class of trades the optimizer never scored. `live_runner`, the
   auto-executor and the backtest now share one knob; also fixes the old
   UTC-hour check that was an hour off outside DST.
3. **Stagnation time-stop** (`TIME_STOP_BARS`, ships dark at 0). Exit after
   N bars with <0.25x stop-distance of progress. Lives in the backtest
   simulator and the optimizer grid (0 vs 12); the nightly walk-forward +
   luck screen decides whether to arm it live via `strategy_weights`. No
   hand-tuned exit rule.

12 new tests.

## c27583b — bot went silent from July 7

Root cause: the July 4 deploy made `_consecutive_losses()` more accurate,
which tripped the breaker on three June-12 losses and halted entries.

- `_check_circuit_breaker()` is now **advisory**: it logs, updates
  `_circuit_breaker` for the dashboard banner, and returns `None` — it never
  refuses an entry.
- `_adaptive_exec_min_score()`: baseline `AUTO_EXEC_MIN_SCORE` (60), −3 pts
  per 4 market-hours of drought, floor 45. Drought is estimated from
  calendar hours × 0.27; `_last_trade_placed_at` seeds from `trades.json`
  and resets on every recorded entry.
- Risk Agent R/R floor lowered 1.5 → 1.0 at scan time (`min_risk_reward`,
  still overridable by the tuner's weights).

Tests updated to the advisory semantics; PM direction tests pinned to the
canonical 60/40 thresholds so `load_dotenv` env pollution from test ordering
can't move them.

**Operator note:** with the breaker advisory-only, `MAX_CONSECUTIVE_LOSSES`
and `DAILY_LOSS_LIMIT_PCT` no longer stop trading — they only light the
dashboard banner. Position sizing and the per-trade risk gates are the
remaining loss controls. This was the deliberate choice ("never stop
trading"); revisit if a drawdown run needs a hard stop.

## Still blocked on operator decisions (carried over from 2026-07-04)

1. **Per-user copy-trading** — execute every signal into each user's own
   account with per-user guards. Architectural; needs a yes.
2. **Cross-venue learning sync** — Railway and the PC learn separately from
   their own fills (intentional: different fill quality). One shared brain
   needs a design choice.
