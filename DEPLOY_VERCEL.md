# Deployment runbook

Two cloud services plus the trading PC:

```
Browser ──> Vercel (dashboard) ──> Railway (api_server.py) <··· reads ··· PC: live_runner.py (trades)
```

| Piece | Where | Notes |
|-------|-------|-------|
| Dashboard | **Vercel** — https://trading-bot-testing.vercel.app | Next.js (`trading-dashboard/`) |
| Backend API | **Railway** — `api_server.py` | from `railway.toml` (NIXPACKS, healthcheck `/api/health`) |
| The bot | **Trading PC** — `live_runner.py` | started by `START.bat`; only this trades |

**Deploys are automatic:** every push to `main` redeploys both the dashboard
(Vercel) and the backend (Railway). Env vars live in each platform's dashboard —
type values by hand; piping corrupts them with `\r\n`.

The Railway backend is kept awake during market hours by the `keep-awake` GitHub
Action, which pings `/api/health` every 10 minutes (set `RAILWAY_BACKEND_URL` as
a repo secret).

## Vercel env vars
Set these on the Vercel project (Root Directory = `trading-dashboard`):

- `TRADING_BOT_API_URL` → the Railway backend URL
- `ALPACA_KEY_ID`, `ALPACA_SECRET`, `ALPACA_PAPER` (`true` for paper)

## Local dev
Run `START.bat` on the PC (bot + API + dashboard). The dashboard works at
http://localhost:3000 against the local API at `:8000`. To expose a local API to
a cloud dashboard during development you can use an ngrok tunnel and point
`TRADING_BOT_API_URL` at it — dev-only; production uses Railway.
