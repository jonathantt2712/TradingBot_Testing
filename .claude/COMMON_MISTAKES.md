# Common Mistakes — read FIRST

Real bugs from this project that cost hours. Don't repeat them.

## 1. Constant risk/reward = dead gate
`target = ATR × mult` with `stop = ATR × mult` makes R/R a constant, so the
`MIN_RISK_REWARD` veto can never trigger. Targets must come from market
structure (session high/low) — see `RiskAgent._target_dist`. Any change to
stop/target math must keep R/R variable per trade.

## 2. Fabricated equity on API failure
Brokers must return `{}` from `get_account()` on failure — NEVER a fake
$100k default. RiskAgent refuses to size without verified equity (fail
closed). A transient API error once meant orders sized against phantom capital.

## 3. `env` block in next.config.js breaks Vercel
It inlines values at BUILD time; cached builds freeze the localhost fallback.
Read `process.env.*` at runtime in server code instead (see lib/bot-api.ts).

## 4. Piping values into `vercel env add` on Windows appends \r\n
The stray `\r` corrupts URLs and breaks fetch. BOT_URL is `.trim()`ed
defensively — keep that, and prefer typing values interactively.

## 5. `pytz` + `datetime.replace(tzinfo=...)` = silently wrong times
Gives LMT offsets (~4 min off). Use `zoneinfo.ZoneInfo` everywhere; build
market-hours boundaries from `datetime.now(ET)`, not from a bar's timestamp.

## 6. Duplicated wiring drifts
main.py and live_runner.py once had separate broker/agent construction — live
mode silently lost regime gating and SPY relative strength. All composition
lives in `trading_bot/bootstrap.py`; never duplicate it.
