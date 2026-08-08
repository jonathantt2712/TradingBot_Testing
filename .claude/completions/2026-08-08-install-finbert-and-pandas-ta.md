# Install the two optional-dependency caveats (FinBERT, pandas-ta)

Date: 2026-08-08
Branch: `claude/telegram-alerts-trading-logic-f89x6e`

Follow-up to the zero-token work: both agents were running degraded because
their optional dependencies were never in `requirements.txt`. Now they are, and
three things had to change for them to actually work.

## What was broken about just `pip install`-ing them

1. **`pandas-ta` no longer installs.** The original package is unmaintained and
   does `from numpy import NaN`, removed in numpy 2 (repo is on numpy 2.4). The
   maintained fork is `pandas-ta-classic`, which imports as
   **`pandas_ta_classic`** — so `import pandas_ta` still failed. `technical_agent`
   now accepts either module name.

2. **`pandas-ta` alone buys nothing.** `_pattern_score` needs the native TA-Lib
   C library too (`_HAS_TALIB_C`), otherwise it returns None regardless. Added
   `ta-lib>=0.6`, which since 0.6 ships prebuilt wheels for Linux/macOS/**Windows**
   (win32/win_amd64/win_arm64, cp311 confirmed on PyPI) — no compiler needed on
   the trading PC.

3. **FinBERT loaded at import time.** `fundamental_agent` built the pipeline as a
   module-level side effect. With transformers actually installed that charged
   **9.0s + a Hugging Face round-trip to every import** — every pytest run, every
   api_server boot, every live_runner start, including runs that never score news.
   Now lazy (`_load_finbert`, built on first use, failure remembered), and run via
   `asyncio.to_thread` because the agent is evaluated concurrently across the
   universe and a 9s inline build would stall the event loop and the API server
   with it. A `threading.Lock` serialises the build so the first scan doesn't
   construct the model once per ticker.

## Verified

- import cost: 9.0s → **0.67s**; `import bootstrap` 0.74s.
- 4 concurrent evaluations, cold: model built **once**, max event-loop stall
  **0.33s** (was 9.4s).
- FinBERT discriminates: "beats earnings, raises guidance" → 84.7,
  "misses estimates, cuts outlook amid probe" → 14.1. Keyword scoring gave a
  much coarser read.
- `patterns` now appears as a real signal (weight 5%) in `TechnicalAgent`'s
  17-signal breakdown; `ta.rsi` / `ta.macd` work under pandas 3.0 / numpy 2.4.
- Full pipeline through `build_manager` → `pm.decide()` runs clean.
- `python -m pytest tests -q` → **523 passed**, 7.4s (no model download).

## Notes for the operator

- **Image size.** torch adds ~750 MB and the model ~440 MB on first use. If a
  Railway build hits a size or time limit, deleting the three FinBERT lines from
  `requirements.txt` is safe — the agent falls back to keyword scoring. The
  `--extra-index-url` for the CPU torch build matters: without it Linux resolves
  the CUDA wheel and pulls ~2.5 GB of GPU libraries this bot never uses.
- **`FINBERT_DISABLED=true`** forces keyword scoring without touching the install.
- **CI**: `tests/conftest.py` sets `FINBERT_DISABLED` for the whole suite so no
  test ever downloads the model. `test_fundamental_agent.py` stubs the pipeline
  to cover the FinBERT branch itself; its keyword tests now opt into the keyword
  path explicitly (they used to rely on FinBERT being absent, which is no longer
  true). CI's first `pip install` will be slower until the pip cache warms.
