# TradingBot_Testing

A multi-agent intraday (day-trading) stock bot plus a Next.js dashboard.

A fleet of specialist agents each score a stock 1–100; the PortfolioManager
blends those scores into a direction, runs the trade through a gauntlet of risk
gates, and routes the survivor to a broker (Alpaca / IBKR) as a bracket order.
The dashboard surfaces the signals and history; trades can require manual
approval.

## Layout
- `trading_bot/` — the Python engine (agents, execution, brokers, backtests).
- `trading-dashboard/` — the Next.js dashboard (deployed on Vercel).

## Docs
- `docs/HOW_IT_WORKS.md` — visual walkthrough of the whole system.
- `.claude/QUICK_START.md` — essential commands.
- `.claude/ARCHITECTURE_MAP.md` — file locations.
- `DEPLOY_VERCEL.md` — deployment runbook (Vercel + Railway).
- `WORKING_TOGETHER.md` — collaboration workflow.

## Run the tests
```bash
cd trading_bot && python -m pytest tests -q
```
