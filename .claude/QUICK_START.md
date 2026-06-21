# Quick Start — essential commands

## Run everything (bot + API + dashboard + tunnel)
```cmd
START.bat                          (double-click; close windows to stop)
```

## Individual pieces
```cmd
cd trading_bot
python live_runner.py              # live bot (DRY RUN unless EXECUTE_LIVE=true)
python main.py AAPL NVDA           # one-shot analysis
python api_server.py               # dashboard backend (:8000)
python backtest_runner.py AAPL     # walk-forward backtest

cd trading-dashboard
npm run dev                        # dashboard UI (:3000)
```

## Tests (run before every push)
```cmd
cd trading_bot && python -m pytest tests -q
```

## Deploy dashboard (Vercel)
```cmd
cd trading-dashboard && npx vercel --prod --yes
```
Live site: https://trading-bot-testing.vercel.app
Backend: Railway (api_server.py) — auto-deploys on push to main.

## Git
Pull before you start, push when you stop. See WORKING_TOGETHER.md.
