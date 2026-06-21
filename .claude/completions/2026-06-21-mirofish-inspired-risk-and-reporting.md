# 2026-06-21 — MiroFish-inspired risk gates, EOD report, correlation graph

Branch: `claude/kind-bohr-s9glhz`. Four features distilled from reviewing the
multi-agent simulation project 666ghj/MiroFish and mapped onto this bot.

## What shipped

1. **Stale-data veto** (`core/freshness.py`, `agents/risk_agent.py`)
   - `bar_staleness` infers a series' cadence and flags a last bar lagging
     wall-clock by > `MAX_BAR_AGE_FACTOR` (default 3x). RiskAgent vetoes stale
     series; skipped in backtests. Fail-closed. (MiroFish: valid-vs-expired facts.)

2. **Agent-disagreement haircut** (`execution/portfolio_manager.py`)
   - `_directional_dispersion` (population std of non-RISK agent scores); size
     scaled to 75% (std>=18) / 50% (std>=25). Strictly risk-reducing.
     (MiroFish: multi-perspective interview.)

3. **EOD ReportAgent** (`agents/report_agent.py`)
   - Bounded facts from the day's audit log + trade stats + memory → LLM desk
     note with deterministic fallback. Wired as `bootstrap.eod_report_loop`,
     published via `TelegramPublisher.send_report`. Runs live + dry-run.
     Config: `EOD_REPORT`, `EOD_REPORT_MIN_BEFORE`. (MiroFish: ReportAgent.)

4. **Data-derived correlation graph** (`data/correlation_graph.py`)
   - `CorrelationGraph.build_from_bars` (return correlation >= `CORRELATION_THRESHOLD`,
     default 0.7) replaces the static `_CORRELATION_GROUPS` for the concentration
     cap; static groups remain the fallback. Rebuilt by
     `bootstrap.correlation_refresh_loop` off the hot path (`CORRELATION_REFRESH_MIN`,
     default 60). (MiroFish: GraphRAG.)

## Not done (deliberately)
- RiskAgent-as-LLM-tool-loop: rejected — sizing is deterministic + safety-critical.
- Async-batched audit logging: rejected — current sync append is fine at this cadence.
- `scenario_runner.py` (pre-trade Monte-Carlo gate): sketched only; not landed.

## Verify
`cd trading_bot && python -m pytest tests -q` → **79 passed**.
New tests: test_freshness, test_report_agent, test_correlation_graph, plus
dispersion cases in test_portfolio_manager.

## Notes
- Container had no deps; installed pandas/numpy/pytest/aiohttp to run tests
  (full requirements.txt has an incompatible `pandas-ta` pin under this Python).
