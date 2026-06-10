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
Live site: https://trading-dashboard-sandy-seven.vercel.app
Tunnel: shun-gigolo-grew.ngrok-free.dev → PC :8000 (started by START.bat)

## Git
Pull before you start, push when you stop. See WORKING_TOGETHER.md.
