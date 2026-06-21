# Trading Dashboard — Setup Guide

## Quick Start (Local)

### 1. Install frontend dependencies
```bash
cd trading-dashboard
npm install
cp .env.example .env.local
npm run dev
```
Open http://localhost:3000

### 2. Start the trading bot API server
```bash
cd trading_bot
pip install fastapi uvicorn  # if not already installed
python api_server.py
```
API runs on http://localhost:8000

---

## Deploy to Vercel

### 1. Push to GitHub
Make sure the `trading-dashboard/` folder is in your GitHub repo.

### 2. Import on Vercel
- Go to https://vercel.com/new
- Import your `TradingBot_Testing` repository
- Set **Root Directory** → `trading-dashboard`
- Add Environment Variable:
  - `NEXT_PUBLIC_BOT_API_URL` = your bot server URL (e.g. Railway, Render, or ngrok for dev)

### 3. Deploy
Click Deploy. Vercel auto-deploys on every push to `main`.

---

## Hosting the Bot API

The Next.js frontend needs to reach your Python API server. Options:

| Option | Free | Persistent |
|--------|------|------------|
| [Railway](https://railway.app) | $5/mo | ✅ |
| [Render](https://render.com) | Free tier | ✅ (spins down) |
| ngrok (dev only) | Free | ❌ |

For Railway: connect your GitHub repo, set **Start Command** to `cd trading_bot && python api_server.py`.

---

## Architecture

```
Browser  ──→  Vercel (Next.js)  ──→  Trading Bot API (FastAPI)
                                              │
                                     trading_bot/agents/
                                     trading_bot/execution/
                                     bot_data/*.json
```

The trading bot pushes fresh recommendations to `/api/recommendations/update` each scan cycle.
The dashboard reads them and displays with live charts + trade confirmation modal.
