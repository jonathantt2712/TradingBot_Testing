# IBKR mode — running the bot against Interactive Brokers

This is the complete runbook for executing through **Interactive Brokers** (TWS
or IB Gateway) instead of Alpaca, including the dashboard **broker toggle** and
everything IBKR mode now supports.

> TL;DR: run TWS/IB Gateway on the same PC as the bot, set the broker to `ibkr`
> (toggle or `.env`), start with the **paper** port `7497`, keep
> `EXECUTE_LIVE=false` until you've watched a few sessions, then turn it on.

---

## 1. Why IBKR runs on your PC

IBKR's API is a **local socket** (`127.0.0.1:<port>`). The bot connects to a TWS
or IB Gateway process running on the *same machine*. A cloud deploy (Railway)
cannot reach a TWS on your desktop, so:

- **`live_runner.py` (the trading brain) must run on the PC that runs TWS.**
- For the dashboard controls (broker toggle, auto-execute, scan button) to drive
  that runner, **`api_server.py` should run on that same PC too** — both share
  `trading_bot/data/`, which is how every runtime toggle reaches the runner.

Per the project rule, only **one** machine runs with `EXECUTE_LIVE=true` at a
time. For IBKR that machine is your PC.

---

## 2. One-time TWS / IB Gateway setup

In **TWS** (or **IB Gateway**) → *Global Configuration → API → Settings*:

| Setting | Value |
|---|---|
| Enable ActiveX and Socket Clients | ✅ checked |
| Read-Only API | ☐ **unchecked** (or the bot can't place orders) |
| Allow connections from localhost only | ✅ checked |
| Socket port | `7497` (TWS paper) · `7496` (TWS live) · `4002` (Gateway paper) · `4001` (Gateway live) |

Log in to TWS with your **paper** credentials first. Leave it running while the
bot runs. (TWS auto-restarts daily; IB Gateway is lighter and better for
unattended sessions — the bot reconnects automatically either way.)

### Market data
US-equity **historical bars** work on paper accounts. If `get_bars` comes back
empty for everything, you likely need a market-data subscription (or enable
delayed data in TWS: *API → Settings → "Use delayed market data"*). The bot
fails closed — no bars means it simply won't trade that name.

---

## 3. Configure the bot

In `trading_bot/.env` (copy from `.env.example`):

```bash
BROKER=ibkr                # or leave alpaca and flip the dashboard toggle
IBKR_HOST=127.0.0.1
IBKR_PORT=7497             # match the TWS socket port above
IBKR_CLIENT_ID=1

EXECUTE_LIVE=false         # keep false until you've watched a few sessions

# Alpaca keys are still recommended even in IBKR mode — the market *scanner*
# and news feed use Alpaca data. Without them the bot trades only the fallback
# watchlist (SPY, QQQ, AAPL, MSFT, NVDA, AMZN, META, TSLA).
ALPACA_API_KEY_ID=...
ALPACA_API_SECRET=...
```

Install deps (includes `ib-insync`) and start:

```bash
cd trading_bot
pip install -r requirements.txt
python api_server.py        # in one terminal (dashboard API + toggles)
python live_runner.py       # in another (the trading brain)
```

On startup the **preflight** tells you exactly what's wrong if IBKR isn't ready:
- `ib_insync` not installed → "run pip install -r requirements.txt"
- TWS/Gateway not reachable → "start TWS, enable the API, check the port"

These surface in the log, in Telegram (if configured), and in the EOD report.

---

## 4. The broker toggle (Alpaca ⇄ IBKR)

On the dashboard's **Trade Recommendations** page there's a broker toggle next to
the Manual/Auto switch:

```
[ 🏛 Alpaca | 🖥 IBKR ]     [ ✋ Manual | 🤖 Auto ]
```

- It writes `data/broker_mode.json` via `POST /api/broker-mode`.
- `live_runner` polls that file every ~10s (`BROKER_SWITCH_POLL_S`). When it
  changes, the runner **ends the current session and restarts it on the new
  broker** — no redeploy, no manual restart.
- **Safety:** if auto-execute is live and positions are open, the runner
  **flattens them on the outgoing broker first**, so nothing is orphaned across
  venues. The toggle asks you to confirm because of this.

Because the toggle is a shared file, the dashboard's API server and the runner
must be on the same machine (they are, per §1). You can also switch without the
UI by setting `BROKER=ibkr` in `.env`, or writing
`data/broker_mode.json` → `{"broker": "ibkr"}`.

The selected broker is reflected in `GET /api/health` (`trading.broker`).

---

## 5. What IBKR mode supports (full parity)

| Capability | IBKR |
|---|---|
| Historical bars (`get_bars`) | ✅ via `reqHistoricalData` |
| Account equity / buying power (`get_account`) | ✅ fail-closed (`{}` when unknown) |
| Bracket entry (market + stop + take-profit) | ✅ `submit_bracket` (OCA children) |
| Positions with live P&L (`get_positions`) | ✅ incl. `market_value` / `unrealized_pl` |
| Open orders with type (`get_open_orders`) | ✅ `stop` / `limit` / … |
| EOD flatten (`close_all_positions`) | ✅ |
| Fill / slippage tracking (`get_order`) | ✅ feeds the scorecard's slippage line |
| **Breakeven-stop lock** | ✅ `cancel_order` + `submit_stop` (auto mode) |
| Auto-reconnect to TWS | ✅ exponential backoff |

The breakeven lock moves a winner's stop to entry once it's up ~1×stop-distance;
it runs only when **auto-execute** is on and `EXECUTE_LIVE=true`.

---

## 6. Going from paper to live

1. Watch several **paper** sessions (port `7497`) — confirm entries, brackets,
   EOD flatten, and the EOD report all look right.
2. Check the **scorecard** (`python scorecard.py` or `/api/scorecard`) — don't
   take it live on `insufficient` confidence.
3. Switch the port to **live** (`7496` TWS / `4001` Gateway) and log TWS into the
   live account.
4. Set `EXECUTE_LIVE=true` on that one PC, start in **Manual** mode, approve a few
   trades by hand, then flip to **Auto** when you're comfortable.

---

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Can't reach IBKR TWS/Gateway at 127.0.0.1:7497" | TWS not running, API not enabled, or wrong port. See §2. |
| "BROKER=ibkr but ib_insync isn't installed" | `pip install -r requirements.txt` in `trading_bot/`. |
| Connects but every ticker is empty | Market-data subscription missing — enable delayed data, or subscribe. |
| Orders never leave | `EXECUTE_LIVE` not `true`, or auto-execute toggle is on Manual. Both are required to auto-place. |
| Account equity 0 / won't size | RiskAgent fails closed without verified equity — check the account is funded (paper accounts are pre-funded) and logged in. |
| Toggle in the UI does nothing | The dashboard API server and `live_runner` must be the same machine sharing `trading_bot/data/`. |
