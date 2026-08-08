# Telegram alerts, auto-mode trading, zero-token pipeline

Date: 2026-08-08
Branch: `claude/telegram-alerts-trading-logic-f89x6e`

Four requests: (1) no Telegram alerts while the market is closed, (2) alerts
only for buys and sells, (3) find out why auto mode never trades, (4) run the
whole app on logic, no LLM tokens.

## 1 + 2 — Telegram is buys and sells, market hours only

Both rules live in `data/telegram_publisher.py` so no caller can bypass them:

- `market_is_open()` — Mon–Fri 09:30–16:00 ET (same window as
  `api_server._is_market_open`). `_notify` drops anything outside it.
- The publisher now exposes exactly two senders: `send_trade_entry` and
  `send_trade_exit`. Everything else was deleted along with its call sites:
  strategy alerts (fired on every 3-min scan), pre-market gapper alerts, the
  Monday weekly summary, the auto-improve notice, health "bot needs attention"
  pushes, and the EOD desk note.

Health issues and the EOD note still reach the operator — via the log
(`health_alert_loop` now logs instead of pushing) and the dashboard health
board. `app/api/internal/telegram/notify` lost its now-unreachable
`market_event` / `weekly_summary` branches.

**Known tradeoff:** an exit that fills just before 16:00 but is *detected* by
the close monitor after 16:00 is suppressed. Rare (EOD flatten is 15:55,
detection follows within ~3 min) but real, and a direct consequence of the
market-hours rule.

## 3 — Why auto mode never bought or sold

Three separate causes, all fixed.

**a. The Auto toggle never reached the bot.** `/api/trade-mode` (dashboard) only
wrote `User.autoExecute` in Neon. Nothing read that field except the Trades page,
which executes recs **in the browser** — so auto mode worked only with the tab
open and did nothing at all overnight. Meanwhile `data/trade_mode.json`, which
`live_runner._auto_execute_enabled()` and `_auto_exec_disarmed_reason()` both
read, stayed `false` forever. Owners' toggles now also POST to the bot's
`/api/trade-mode` (shared-bot control → owner-gated, per ARCHITECTURE_MAP);
viewers keep the per-user, browser-side behaviour.

**b. A disarmed executor was invisible.** `AUTO_EXECUTE_ON_RAILWAY` defaults
off, so even an armed toggle can be inert. `GET/POST /api/trade-mode` and
`/api/health` now report `armed` + `disarmed_reason`, and the toggle shows a
warning toast + caution border instead of pretending trades are flowing.

**c. Shorts could never be sized or selected.** `composite_score` is a LONG-ness
scale (100 = strong long, 0 = strong short), but two places read it as raw
quality:
  - the scanner's min-score gate (`score < effective_min`) rejected every
    *strong* short and kept the marginal ones;
  - `_kelly_qty` used `p = score/100` as win probability, so a good short got a
    negative Kelly fraction → `qty = 0` → skipped by `_auto_exec_candidates`.
  - the pre-market scan also stamped gap-downs with a bullish-looking score.

Added `_conviction(score, direction)` and routed all three through it.

## 4 — No LLM tokens

`USE_LLM_AGENTS` already defaulted false and gated Fundamental / Vision /
Decision. The one uncovered path was `EODReportAgent`, which called the LLM
whenever a key existed in the environment. It now takes `llm_enabled`
(bootstrap passes `settings.use_llm_agents`), so a configured key is no longer
implicit consent to spend it. `preflight_checks` also stopped reporting a
missing LLM key as a problem — zero-token is the normal configuration now; it
only warns when `USE_LLM_AGENTS=true` and no key is set.

Every `LLMAdapter` construction in the app now flows through `bootstrap.py` and
is gated. FinBERT (the fundamental fallback) runs locally, no API.

## Verification

`cd trading_bot && python -m pytest tests -q` → 517 passed, 1 skipped.
New/updated tests: `test_telegram_publisher.py` (market-hours gate, trade-only
senders), `test_kelly_qty.py` (direction-aware sizing), `test_auto_executor.py`
(trade-mode arming report), `test_report_agent.py` (a key present ≠ tokens
spent), `roles.test.ts` (owner arms the bot, viewer doesn't).
