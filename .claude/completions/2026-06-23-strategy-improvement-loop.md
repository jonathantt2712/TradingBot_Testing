# 2026-06-23 — Strategy-improvement loop + per-agent scorecard

Request: "add a strategy loop that improves each agent." Confirmed scope with the
operator before building (the existing `WeightTuner` already adapts per-agent
weights, but only the instant a trade closes — there was no periodic loop):
- **Scope chosen:** periodic re-tune **+** per-agent scorecard.
- **Apply mode chosen:** write-through (live) — adaptations take effect
  immediately via `strategy_weights.json`, same channel the WeightTuner already
  uses; PortfolioManager reads it TTL-cached.

## What was added
- `core/agent_scorecard.py` — `compute_agent_scorecards(closed_trades, weights, window)`,
  a pure, fail-soft function. Per agent over the tuner's rolling window: directional
  hit rate, sample size, the live weight/multiplier in force, and avg P&L on trades
  it agreed with. Correctness rule mirrors `WeightTuner` exactly (score ≥ 50 = leaned
  LONG, ≤ 50 = leaned SHORT; correct when that lean matches the win) so the surfaced
  numbers line up with the multipliers actually applied.
- `api_server.py`:
  - `_strategy_improvement_loop()` — new background loop (default every
    `STRATEGY_LOOP_INTERVAL_MIN`=60 min). Each tick re-runs the WeightTuner over the
    full closed-trade history (`_drive_weight_tuner`, write-through to
    strategy_weights.json) and refreshes the scorecard. Registered + cancelled in
    `lifespan` alongside the other loops; wrapped in try/except like its siblings.
  - `_refresh_agent_scorecards()` — loads closed trades + live weights, computes the
    cards, persists `data/agent_scorecards.json`.
  - `GET /api/agent-scorecards` — serves the persisted scorecard; lazily recomputes
    if the loop hasn't written it yet (fresh process).

## Why this shape
Reuses the proven, already-tested `WeightTuner` rather than inventing a second
learning path; the only genuinely new logic is the per-agent read-model, which is a
pure function under test. Kept out of every agent's internals (the more invasive
"tune each agent's own knobs" option) to stay low-risk on the money path.

## Verify
- `tests/test_agent_scorecard.py` — 9 tests: hit-rate counting, SHORT-lean scoring,
  perfect/zero-agent separation, agreed-only avg P&L, window cap, weight/multiplier
  passthrough, no-evaluations ignored, and `_refresh_agent_scorecards` persisting the
  file end-to-end.
- Full suite: **236 passed, 1 skipped** (was 227). App builds, route registered,
  loop wired into lifespan.

## Not done (out of scope unless asked)
- No dashboard view for the scorecard yet — the `/api/agent-scorecards` endpoint is
  ready for one to consume.
- `STRATEGY_LOOP_INTERVAL_MIN` left undocumented in `.env.example` to match the
  existing convention there (the other loop-interval vars aren't listed either).
