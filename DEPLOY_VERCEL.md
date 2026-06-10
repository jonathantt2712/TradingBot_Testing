# Put the dashboard live on Vercel

> **STATUS: ✅ FULLY SET UP AND VERIFIED (2026-06-10).**
> Live site: **https://tradingbot2026.vercel.app**
> Backend:  **https://tradingbot-api-ql85.onrender.com** (Render free tier,
> deployed from render.yaml; kept awake during market hours by the
> keep-awake GitHub Action)
>
> The ngrok tunnel is RETIRED — the backend runs in the cloud now. The bot
> (live_runner.py) still runs on the trading PC via START.bat.
>
> **Deploys are automatic**: every `git push` to `main` redeploys BOTH the
> dashboard (Vercel) and the backend (Render). Env vars live in each
> platform's dashboard (type values by hand; piping corrupts them with \r\n).
>
> The steps below describe the old tunnel setup — reference only.

How it works: the **website runs on Vercel**, the **bot runs on your PC** (START.bat,
same as always). A free ngrok tunnel connects them. When START.bat is running,
your Vercel site shows live data; when your PC is off, the site still loads but
shows demo data.

```
Browser ──> your-app.vercel.app ──> ngrok tunnel ──> your PC :8000 (api_server)
```

## One-time setup (~10 minutes)

### 1. Get a free ngrok tunnel with a permanent address

1. Sign up (free): https://dashboard.ngrok.com/signup
2. Install ngrok — in cmd:
   ```cmd
   winget install ngrok.ngrok
   ```
3. Connect your account (copy YOUR token from https://dashboard.ngrok.com/get-started/your-authtoken):
   ```cmd
   ngrok config add-authtoken YOUR_TOKEN_HERE
   ```
4. Claim your free permanent domain at https://dashboard.ngrok.com/domains
   → click "Create Domain" → you get something like `your-name.ngrok-free.app`
5. Put that domain in the `.env` file in this folder (no https://, just the domain):
   ```
   NGROK_DOMAIN=your-name.ngrok-free.app
   ```

From now on START.bat automatically opens the tunnel in a 4th window.

### 2. Deploy the dashboard to Vercel

In cmd:

```cmd
cd "C:\Users\itait\Claude\Projects\trading bot\trading-dashboard"
npx vercel login
npx vercel --prod
```

Accept the defaults when it asks questions. At the end it prints your live URL,
e.g. `https://trading-dashboard-xxxx.vercel.app`.

### 3. Tell Vercel where your bot is

Still in the `trading-dashboard` folder (replace the domain with yours from step 1):

```cmd
npx vercel env add TRADING_BOT_API_URL production
```
→ when prompted for the value, enter: `https://your-name.ngrok-free.app`

The dashboard also shows your Alpaca account/positions, so add those too:

```cmd
npx vercel env add ALPACA_KEY_ID production
npx vercel env add ALPACA_SECRET production
npx vercel env add ALPACA_PAPER production
```
→ use the same values as in `trading-dashboard\.env.local` (ALPACA_PAPER = `true`).

Then redeploy once so the settings take effect:

```cmd
npx vercel --prod
```

## Daily use

1. Double-click **START.bat** — bot, API server, dashboard, and tunnel all start.
2. Open your Vercel URL from anywhere (phone, laptop, anywhere) — live data.

That's it. No START.bat running = site shows demo data instead.

## Troubleshooting

- **Site shows demo data** → START.bat isn't running, or the tunnel window shows an
  error, or `NGROK_DOMAIN` in `.env` doesn't match `TRADING_BOT_API_URL` on Vercel.
- **Tunnel window says "domain not found"** → the domain in `.env` must exactly match
  the one claimed at dashboard.ngrok.com/domains.
- **Changed the ngrok domain?** → update the Vercel env var:
  `npx vercel env rm TRADING_BOT_API_URL production` then add it again, redeploy.
