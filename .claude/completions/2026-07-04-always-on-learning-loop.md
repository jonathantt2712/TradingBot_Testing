# Always-on learning loop — 2026-07-04

Goal: the bot improves continuously — running backtests and learning from its
own trades — with no human in the loop, but with hard statistical guard rails.

## The full cycle (per venue: Railway api_server, or the PC's local api_server)

**On every closed trade** (`_check_and_close_trades`):
1. `WeightTuner.update_from_trades` — re-weights the agents from resolved
   outcomes and learns per-regime strategies (`regime_params`), written to
   `strategy_weights.json`.
2. `_update_strategy_weights` (win-rate self-tuner) — refines ATR multiples /
   min-score from the last 20 closed trades and updates `win_rate_30d` +
   directional bias, which feed Kelly sizing and the DecisionAgent's
   performance context. Now event-driven (previously only ran every 3rd scan)
   and only counts an update when NEW outcomes exist — `update_count` gates
   Kelly's "no size-up without track record", so it must reflect real trades,
   not function invocations.

**Hourly** (`_strategy_improvement_loop`): tuner re-run + per-agent scorecards.

**Nightly, weekdays**:
- 17:00 ET — auto-backtest (`backtest_intraday.py`, existing).
- 18:00 ET (`AUTO_OPTIMIZE_HOUR_ET`) — **new** `_auto_improve_cycle()`:
  1. Runs `optimize_strategy.py` (walk-forward: tune on first 70%, validate on
     held-out 30%; LLM agents off, so no API cost).
  2. Auto-applies the best params via `_apply_optimizer_params(require_validated=True)`
     — the shared helper behind the dashboard's "Apply Optimal Params" button.
  3. Sends a Telegram alert when params were applied.

**Hot reload into live trading** (existing, verified): PortfolioManager
re-reads thresholds/weights within 60s (`_tuned` TTL), RiskAgent reads ATR
multiples per plan, live_runner's `strategy_refresh_loop` syncs hourly — all
gated on `live_tuning_active`, which the auto-apply sets.

## Guard rails on autonomous application

- **Walk-forward only**: the auto path rejects any result without a held-out
  (OOS) split, and any split the optimizer flagged as too thin
  (`validated: false`, < MIN_OOS_TRADES). The manual dashboard button keeps
  its looser guard (operator judgment).
- **Positive OOS profit required** (both paths, as before).
- **Manual overrides win**: fields locked via the dashboard
  (`manual_overrides`) are never overwritten by the auto-apply.
- **Kill switch**: `AUTO_OPTIMIZE=false` reverts to manual-apply-only.

## Bug fixed along the way

`apply_optimal_params` read `optimization_results.json` from the repo root,
but the optimizer writes it to `volume_dir() or repo root` — on Railway with a
volume the dashboard's Apply button read a missing/stale file. The shared
helper now reads volume-aware (`_VOLUME or _REPO_ROOT`), same as the writer.

## Notes

- Learning is **per venue**: the Railway server learns from Railway's trades,
  the PC's local api_server from the PC's — each writes its own
  `strategy_weights.json` via `core.paths.data_dir()`. They are not synced
  across machines (by design: different fill quality → different parameters).
- New env knobs documented in `.env.example`: `AUTO_OPTIMIZE`,
  `AUTO_OPTIMIZE_HOUR_ET`.

## Tests

`tests/test_auto_improve.py` (7 new): auto path rejects unvalidated /
negative-OOS / thin-holdout results; applies validated positive and activates
live tuning; respects manual overrides; operator path still accepts
unvalidated-positive; self-tuner no-ops on unchanged trade sets.
Suite: **424 passed / 1 skipped**.
