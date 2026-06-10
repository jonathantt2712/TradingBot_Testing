# Research-Derived Trading Filters — Implementation Notes

Generated: 2026-06-08

## What Was Changed

Four academic research findings were translated into concrete code changes across
`technical_agent.py`, `backtest_30day.py`, `optimize_backtest.py`, and
`portfolio_manager.py`.

---

## 1. PEAD Open-Noise Filter (Luo et al. 2023)

**Finding:** Retail investors act as contrarians around earnings events. This creates
30-60 minutes of noisy, counter-trend volume at the open before institutional
direction is established (Post-Earnings Announcement Drift).

**What we changed:**
- `backtest_30day.py` and `optimize_backtest.py`: Skip any entry bar where
  `entry_et.hour == 9` (i.e., 9:30–9:59 ET). No signals executed in the first
  30 minutes of RTH.

**Expected impact:** Fewer trades (some open-range trades eliminated), but higher
quality setups because we're trading with institutional flow rather than against
retail noise.

---

## 2. Lottery Stock / CPT Filter (Reichenbach & Walther 2023/2024)

**Finding:** Stocks with large recent price surges + high social volume display
"lottery ticket" behavior — retail CPT (Cumulative Prospect Theory) drives violent
mean-reversions after the pump.

**What we changed:**

*`technical_agent.py` — `_lottery_penalty()`*
- Detects: price move >12% in last 20 bars AND projected volume >2.5× 20-day avg
- Applies a score penalty that pulls composite toward 50 (neutral), magnitude 0–30
- Logged as `[LOTTERY pen=XX]` in rationale

*`optimize_backtest.py` — `EvalRecord.lottery` + `replay_records()`*
- `_is_lottery()` helper stores the flag at collection time
- In `replay_records()`: if `rec.lottery == True`, use `sl_mult × 0.6` instead
  of the base SL multiplier — tighter stop to survive mean-reversions

**Expected impact:** On lottery setups, either the lower composite score filters
them out entirely, or if they pass, the tighter stop limits damage from reversals.

---

## 3. Transaction Drag / Volume Confirmation Gate (Barber & Odean)

**Finding:** Only ~1% of active traders are predictably profitable net of fees.
High-frequency low-conviction signals destroy EV through transaction costs and
slippage.

**What we changed:**

*`technical_agent.py` — `_volume_confirm()` + `vol_confirm` signal (weight: 0.06)*
- Returns 60–90 if current bar volume ≥ 1.3× rolling 24-bar avg (high conviction)
- Returns 20–49 if below threshold (penalises low-volume setups in composite)

*`backtest_30day.py` and `optimize_backtest.py` — hard gate in walk-forward loop*
- Skip entry entirely if `bar_vol < 1.3 × rolling_20bar_avg`
- This is a hard filter on top of the soft signal in TechnicalAgent

**Expected impact:** Fewer trades overall, but each trade has confirmed liquidity
and institutional participation — better slippage profile in live trading.

---

## 4. Retail Attention Classifier (Gao et al. 2023)

**Finding:** Retail-attention-driven momentum is exploitable intraday but carries
huge overnight gap-down risk. Retail-driven trends require higher-conviction entry.

**What we changed:**

*`technical_agent.py` — `_is_retail_driven()`*
- Detects: price move >8% in last 3 trading days AND volume surge >2.0×
- Stores `retail_driven: True` and `retail_surcharge: 5.0` in agent `.data`
- Logged as `[RETAIL-DRIVEN +5thr]` in rationale

*`portfolio_manager.py` — `_direction(retail_surcharge=)`*
- Reads `retail_surcharge` from TechnicalAgent eval data
- Adds +5 to `LONG_THRESHOLD` and -5 to `SHORT_THRESHOLD` for retail-driven tickers
- We're already day-trade-only (forced EOD close), which is the right behaviour
  for retail-attention setups per the research

**Expected impact:** On retail-driven momentum, we only enter on the strongest
signals (composite ≥ LONG_THRESHOLD + 5), avoiding the marginal setups that end
in gap-down losses.

---

## Before vs After (Baseline — pre-filter run)

| Metric         | Before filters |
|----------------|---------------|
| Total trades   | 53            |
| Win rate       | 28.3%         |
| Profit factor  | 1.42          |
| Sharpe ratio   | 2.59          |
| Total P&L      | +$1,815       |
| EV/trade       | -$41 (theoretical) |

*Post-filter results will be in `backtest_optimal.json` after the current optimizer run.*

---

## Files Modified

| File | Change |
|------|--------|
| `trading_bot/agents/technical_agent.py` | Added `_volume_confirm()`, `_lottery_penalty()`, `_is_retail_driven()` helpers; new `vol_confirm` signal; lottery and retail surcharge applied in composite |
| `trading_bot/backtest_30day.py` | PEAD open-noise skip (hour==9); volume confirmation hard gate (1.3×) |
| `trading_bot/optimize_backtest.py` | Same PEAD + volume gates in `collect_records`; `EvalRecord.lottery` field; `_is_lottery()` helper; dynamic SL (0.6×) in `replay_records` |
| `trading_bot/execution/portfolio_manager.py` | `_direction()` now accepts `retail_surcharge`; reads from TechnicalAgent eval data |
