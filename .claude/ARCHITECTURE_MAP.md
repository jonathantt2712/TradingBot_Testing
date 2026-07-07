# Architecture Map

```
Browser ─> Vercel (Next.js dashboard) ─> Railway cloud (api_server.py)
                                          PC: live_runner.py (the bot — trades)
```
Backend: Railway (from railway.toml; healthcheck /api/health). The keep-awake
GitHub Action pings /api/health every 10 min during market hours; tests.yml
runs pytest + vitest/tsc on every push/PR. Dashboard:
https://trading-bot-testing.vercel.app

## trading_bot/  (Python — the engine)
- `bootstrap.py`        — ALL composition: env loading, build_broker/build_manager,
                          refresh_market_context, eod_flatten_loop, heartbeat_loop
- `live_runner.py`      — live mode: scan → evaluate → heartbeat → rescan → EOD flatten;
                          entries wall-clock gated (no entries post-flatten/off-hours)
- `main.py`             — one-shot scan
- `api_server.py`       — FastAPI backend (:8000). Background loops: market scan,
                          trade-close monitor, trailing stops (PAPER-only),
                          broker↔trades.json reconciliation, EOD review,
                          auto-executor (paper), nightly backtest 17:00 ET,
                          nightly auto-improve 18:00 ET, heartbeat watchdog
- `config/settings.py`  — every env var → typed Settings
- `core/paths.py`       — `data_dir()`: THE resolver for ALL runtime state files
                          (volume-backed on Railway). Never build data/ paths by hand.
- `core/slippage.py`    — measured entry slippage from real fills → backtest costs
- `core/textsafe.py`    — sanitize untrusted news text before LLM prompts
- `agents/`             — fundamental, technical, vision, insider, squeeze, macro,
                          liquid, regime, risk (risk = gate + plan builder);
                          RegimeSnapshot.vix_is_proxy flags VIXY fallback readings
- `execution/`
  - `portfolio_manager.py` — composite blend → direction → risk veto → entry guard
                             (dup positions, MAX_OPEN_POSITIONS, kill switch —
                             persisted per ET day, restart-safe)
  - `alpaca_broker.py` / `ibkr_broker.py` — get_bars/account/positions, brackets
                          (idempotent client_order_id), replace_order_stop for the
                          breakeven lock; FAIL CLOSED on account errors
- `validation/`         — permutation/randomization tests; the auto-apply luck screen
- `tests/`              — pytest; run before pushing (CI enforces)

## Self-improvement loop (per venue — Railway and PC each learn from own fills)
1. Trade closes → WeightTuner (agent weights, per-regime params) + win-rate
   self-tuner (ATR/score refinements, Kelly inputs). Event-driven.
2. Nightly 17:00 ET backtest; 18:00 ET walk-forward optimizer (AUTO_OPTIMIZE).
3. Auto-apply ONLY if: walk-forward validated + positive held-out PnL +
   luck-screen p ≤ AUTO_APPLY_MAX_P + field not manually locked.
4. Applied params hot-reload into live trading (live_tuning_active gate).
5. Every apply/reject decision → improvement_history.jsonl → dashboard
   Learning page "Self-Improvement Timeline".

## trading-dashboard/  (Next.js — the face)
- Auth: invite-only signup (SIGNUP_INVITE_CODE), owner/viewer roles —
  shared-bot controls (trade-mode, broker switch, optimizer, breaker reset)
  are OWNER-only; viewers read + trade their own Alpaca account
- `app/api/bot/*`       — server routes proxying to TRADING_BOT_API_URL (no CORS)
- `app/api/alpaca/*`    — server routes using the SIGNED-IN USER's creds
                          (decrypted from DB per request — never in the JWT)
- Execute flow: bot entry-check (409 blocks) → user's Alpaca order
  (idempotent client_order_id) → best-effort record with the bot
- `lib/bot-api.ts`      — the proxy client (trims env URL, ngrok skip header)
- Deployed via Vercel CLI (no git integration); env vars live in Vercel

## Root
- `START.bat`           — launches all four windows
- `tunnel.bat`          — self-healing ngrok tunnel (retries every 15s)
- `.env`                — secrets (NOT in git); `.env.example` documents them
- `WORKING_TOGETHER.md` — collaboration workflow
- `DEPLOY_VERCEL.md`    — deployment runbook
