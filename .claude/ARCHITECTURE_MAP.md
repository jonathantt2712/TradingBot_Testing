# Architecture Map

```
Browser ─> Vercel (Next.js dashboard) ─> Render cloud (api_server.py, 24/7)
                                          PC: live_runner.py (the bot — trades)
```
Render service: https://tradingbot-api-ql85.onrender.com (free tier; a GitHub
Action pings it every 10 min during market hours so it never sleeps mid-session)

## trading_bot/  (Python — the engine)
- `bootstrap.py`        — ALL composition: env loading, build_broker/build_manager,
                          refresh_market_context, eod_flatten_loop
- `live_runner.py`      — live mode: scan → evaluate → heartbeat → rescan → EOD flatten
- `main.py`             — one-shot scan
- `api_server.py`       — FastAPI backend for the dashboard (:8000)
- `config/settings.py`  — every env var → typed Settings
- `agents/`             — fundamental, technical, vision, social, liquid, regime,
                          risk (risk = gate + plan builder, not directional)
- `execution/`
  - `portfolio_manager.py` — composite blend → direction → risk veto → entry guard
                             (dup positions, MAX_OPEN_POSITIONS, daily-loss kill switch)
  - `alpaca_broker.py` / `ibkr_broker.py` — get_bars/account/positions, brackets,
                          close_all_positions; FAIL CLOSED on account errors
- `tests/`              — pytest; run before pushing

## trading-dashboard/  (Next.js — the face)
- `app/api/bot/*`       — server routes proxying to TRADING_BOT_API_URL (no CORS)
- `app/api/alpaca/*`    — server routes using ALPACA_KEY_ID/SECRET
- `lib/bot-api.ts`      — the proxy client (trims env URL, ngrok skip header)
- Deployed via Vercel CLI (no git integration); env vars live in Vercel

## Root
- `START.bat`           — launches all four windows
- `tunnel.bat`          — self-healing ngrok tunnel (retries every 15s)
- `.env`                — secrets (NOT in git); `.env.example` documents them
- `WORKING_TOGETHER.md` — collaboration workflow
- `DEPLOY_VERCEL.md`    — deployment runbook
