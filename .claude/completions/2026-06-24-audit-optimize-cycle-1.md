# Audit & Optimize — Cycle 1 (2026-06-24)

## Baseline vs. Post-Optimization Test Results

| Metric | Baseline | Post-Optimization |
|---|---|---|
| Passing | 240 | 298 |
| Failing | 2 | 0 |
| Skipped | 7 | 2 |
| Collected | 249 | 300 |

> **Note**: Baseline measured after installing missing env deps (pytest, numpy, pandas,
> aiohttp, python-dotenv) required to collect tests. Installing fastapi (also in
> requirements.txt) resolved a second-order import failure in the scorecard test.

---

## Files Modified

### 1. `trading_bot/agents/technical_agent.py` — Bug fix
**Problem**: `_macd_hist()` has two code paths: one via `pandas-ta` (which returns
`None` on short frames, already guarded) and a pure-EWM fallback (no guard). On a
3-bar series the fallback computed a non-zero value, failing
`test_macd_hist_short_frame_returns_zero`.

**Fix**: Added `if len(close) < 34: return 0.0` at the top of `_macd_hist`, before
both paths. MACD requires slow(26) + signal(9) − 1 = 34 bars to produce a meaningful
histogram; anything shorter returns neutral 0.0.

### 2. `trading_bot/execution/portfolio_manager.py` — Observability
**Problem**: When all agent weights sum to zero (all feeds failed, weights
misconfigured, etc.), `_composite()` silently returned the neutral 50.0 with no
log, masking misconfiguration during on-call review.

**Fix**: Replaced the silent ternary with an explicit `if not den:` block that
emits `logger.warning(...)` before returning 50.0.

### 3. `trading_bot/tests/test_portfolio_manager.py` — New tests (+3)

| Test | What it covers |
|---|---|
| `test_composite_all_none_returns_neutral` | All-None inputs → 50.0 (no ZeroDivisionError) |
| `test_composite_minimum_confidence_all_agents` | Min-confidence blend still produces valid composite |
| `test_composite_squeeze_boost_applies_at_low_confidence` | Boost fires before confidence gate, even at confidence=0.05 |

### 4. `trading_bot/tests/test_risk_agent.py` — New tests (+4)

| Test | What it covers |
|---|---|
| `test_zero_atr_refuses_to_plan` | Truly flat bars (bar_range=0) → ATR=0 → returns None (no division by zero) |
| `test_volatility_multiplier_clips_high_volatility` | atr_pct=10% → clipped to 0.5x floor |
| `test_volatility_multiplier_clips_low_volatility` | atr_pct=0.1% → clipped to 1.5x ceiling |
| `test_volatility_multiplier_neutral_at_baseline` | atr_pct=1.5% → exactly 1.0x |

---

## Next High-Priority Optimization Vectors

1. **Kelly multiplier edge-case coverage**: `_kelly_multiplier` has a negative-Kelly
   path (returns 0.25x) that's never exercised by a test. Add a `monkeypatch` test
   that injects a strategy_weights.json with low win-rate + low R/R to confirm the
   quarter-size guard fires.

2. **`test_freshness.py` coverage gap**: Verify that the stale-data veto (freshness
   check) correctly fires on weekend/halted bars and that the health report is
   emitted. The current test passes `backtest_mode=True` which bypasses freshness —
   add a live-mode test with a mocked timestamp.

3. **Disagreement haircut boundary tests**: The `_disagreement_haircut` applies 0.75×
   at std ≥ 18 and 0.5× at std ≥ 25. Tests exist for high-conflict scenarios but not
   for the exact boundary values (std = 18, std = 25). Add parametrized boundary tests.

4. **`_target_dist` with room = 0**: When price is *exactly* at the session high/low
   (room = 0.0, not within 0.25×ATR breakout territory), the function should fall back
   to the ATR target. Add a test for this boundary condition to confirm the guard at
   `room <= atr * 0.25` handles it correctly.

5. **Correlation concentration cap with dynamic graph**: `test_concentration_cap_*`
   tests use the static hardcoded group map. Add a test that injects a live
   `CorrelationGraph` with a computed matrix to verify the dynamic path.
