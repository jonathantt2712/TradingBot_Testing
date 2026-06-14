# Per-User Position Sizing at Execute Time

## Problem

The trading bot (Railway) generates shared recommendations once for all
dashboard users (`recommendations.json`, served via `/api/recommendations`).
Each recommendation includes `risk.qty`, `risk.dollar_risk`, and
`risk.risk_reward`, all computed by `RiskAgent.build_plan`
(`trading_bot/agents/risk_agent.py`) using **the bot's own account equity**
(`ALPACA_API_KEY_ID`/`ALPACA_API_SECRET` on Railway).

When a dashboard user clicks "Execute", `app/api/bot/execute/route.ts`
already submits a bracket order to **that user's own Alpaca account**
(via `getAlpacaCreds()` + `submitBracketOrder`) — this part is correct and
unchanged. However, it submits the bot's `qty`, which is sized for the
bot's account equity, not the executing user's. A user with $1,000 could
receive an order sized for a $100,000 account.

## Goal

When executing a recommendation, recompute `qty` based on the **executing
user's own account equity**, using the same risk formula the bot uses,
before submitting the order.

## Design

### A. Risk sizing helper — `trading-dashboard/lib/risk.ts` (new file)

```ts
const RISK_PER_TRADE_PCT = Number(process.env.RISK_PER_TRADE_PCT ?? '0.01')
const MAX_POSITION_PCT   = Number(process.env.MAX_POSITION_PCT   ?? '0.20')

/** Mirrors RiskAgent.build_plan's sizing formula (trading_bot/agents/risk_agent.py). */
export function sizePosition(equity: number, entry: number, stopLoss: number): number {
  const perShareRisk = Math.abs(entry - stopLoss)
  if (perShareRisk <= 0 || entry <= 0 || equity <= 0) return 0

  const riskUsd          = equity * RISK_PER_TRADE_PCT
  const qtyByRisk        = riskUsd / perShareRisk
  const qtyByExposure    = (equity * MAX_POSITION_PCT) / entry

  return Math.floor(Math.min(qtyByRisk, qtyByExposure))
}
```

### B. Config — new Vercel env vars

```
RISK_PER_TRADE_PCT=0.01
MAX_POSITION_PCT=0.20
```

(Mirrors `trading_bot/.env`'s `MAX_RISK_PER_TRADE_PCT` and `MAX_POSITION_PCT`.
Keep these two in sync manually if the bot's risk config ever changes —
no automatic sync.)

### C. `app/api/bot/execute/route.ts` changes

After loading `creds` and before submitting the order:

1. Call `getAccount(creds)` to get the user's `equity` (parse as float).
2. Compute `qty = sizePosition(equity, body.entry, body.stop_loss)`.
3. If `qty <= 0`, return early:
   ```ts
   return NextResponse.json(
     { success: false, order_id: '', message: 'Your account balance is too small to size this trade within risk limits' },
     { status: 422 },
   )
   ```
4. Use this `qty` (not `body.qty`) for `submitBracketOrder` and the
   best-effort `botPost('/api/execute', ...)` call.
5. Include `qty` in the response.

If `getAccount(creds)` itself fails (network/auth error), return the
existing 401-style error path — don't fall back to `body.qty`.

### D. Type changes — `types/trading.ts`

`ExecuteResponse` gains:
```ts
qty: number
```

### E. UI changes

- `components/trades/ConfirmModal.tsx`:
  - Success toast uses `res.qty` instead of `trade.risk.qty`:
    ```ts
    toast.success(`Trade executed: ${trade.direction} ${res.qty}x ${trade.ticker}`, {
      description: `Order ID: ${res.order_id}`,
    })
    ```
  - Add a small note near the Quantity field:
    > "Quantity will be sized to your account balance at execution."
  - On `qty <= 0` error (422), `toast.error` already handles this via the
    existing catch block — verify the message surfaces correctly.

- `app/trades/page.tsx` `handleBuyAll`:
  - Per-trade success toast uses the response's `qty`:
    ```ts
    const res = await api.execute({ ... })
    toast.success(`${trade.direction} ${trade.ticker}`, {
      description: `${res.qty} shares @ ${trade.risk.entry}`,
    })
    ```
  - On 422 (`qty <= 0`), treat as a non-fatal per-trade failure (increment
    `failed`, show `toast.error` with the message), not a hard stop for
    the whole "Buy All" loop.

## Out of scope

- No changes to `trading_bot/` (Python) — `risk.qty` in recommendations
  remains the bot's own sizing, used only as a preview/display value and
  for `entry`/`stop_loss`/`take_profit` (shared, account-independent).
- No database schema changes.
- No changes to how recommendations are generated or filtered.

## Testing

- Manually verify: log in as a user with a small paper account, execute a
  recommendation, confirm the submitted Alpaca order qty matches
  `sizePosition(equity, entry, stop_loss)`, not the displayed `risk.qty`.
- Verify the `qty <= 0` path: a recommendation with `entry`/`stop_loss`
  far enough apart that even `MAX_POSITION_PCT` of a very small account's
  equity rounds down to 0 shares → execute returns 422, no Alpaca order
  submitted, error toast shown.
- `cd trading_bot && python -m pytest tests -q` — no Python changes
  expected to affect this, but run per project convention since
  `trading_bot/agents/risk_agent.py` was read as reference (not modified).
