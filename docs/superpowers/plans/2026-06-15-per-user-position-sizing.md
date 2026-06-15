# Per-User Position Sizing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a user executes a trade recommendation, recompute `qty` based on
that user's own Alpaca account equity (not the bot's), before submitting the
order — per `docs/superpowers/specs/2026-06-15-per-user-position-sizing-design.md`.

**Architecture:** Add a pure sizing helper (`lib/risk.ts`) mirroring
`RiskAgent.build_plan`'s formula. Call it from `app/api/bot/execute/route.ts`
using the executing user's `getAccount(creds)` equity, before
`submitBracketOrder`. Return the computed `qty` to the client and use it in
the UI toasts. A `qty <= 0` result returns HTTP 422 with no order submitted.

**Tech Stack:** Next.js App Router (TypeScript), Vitest for tests, existing
`lib/alpaca.ts` Alpaca REST client.

---

### Task 1: Risk sizing helper — `lib/risk.ts`

**Files:**
- Create: `trading-dashboard/lib/risk.ts`
- Test: `trading-dashboard/tests/lib/risk.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect } from 'vitest'
import { sizePosition } from '@/lib/risk'

describe('lib/risk sizePosition', () => {
  it('sizes by risk-per-trade when that is the binding constraint', () => {
    // equity=10000, risk 1% = $100, perShareRisk = 50-48 = 2 -> 50 shares by risk
    // exposure cap: 20% of 10000 / 50 = 40 shares -> exposure is binding
    const qty = sizePosition(10000, 50, 48)
    expect(qty).toBe(40)
  })

  it('sizes by risk-per-trade when exposure is not binding', () => {
    // equity=100000, risk 1% = $1000, perShareRisk = 2 -> 500 shares by risk
    // exposure cap: 20% of 100000 / 50 = 400 shares -> exposure is binding again
    // use a wider stop so risk is binding: perShareRisk = 10 -> 100 shares by risk
    // exposure cap: 20% of 100000 / 50 = 400 shares -> risk (100) binds
    const qty = sizePosition(100000, 50, 40)
    expect(qty).toBe(100)
  })

  it('returns 0 when equity is zero or negative', () => {
    expect(sizePosition(0, 50, 48)).toBe(0)
    expect(sizePosition(-100, 50, 48)).toBe(0)
  })

  it('returns 0 when entry equals stopLoss (zero per-share risk)', () => {
    expect(sizePosition(10000, 50, 50)).toBe(0)
  })

  it('returns 0 when entry is zero or negative', () => {
    expect(sizePosition(10000, 0, -1)).toBe(0)
  })

  it('floors fractional share counts', () => {
    // equity=1000, risk 1% = $10, perShareRisk = 3 -> 3.33 -> floor to 3
    // exposure cap: 20% of 1000 / 50 = 4 -> risk (3) binds
    const qty = sizePosition(1000, 50, 47)
    expect(qty).toBe(3)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd trading-dashboard && npx vitest run tests/lib/risk.test.ts`
Expected: FAIL — `Cannot find module '@/lib/risk'`

- [ ] **Step 3: Write minimal implementation**

```typescript
/**
 * Mirrors RiskAgent.build_plan's sizing formula
 * (trading_bot/agents/risk_agent.py) — sizes a position to the EXECUTING
 * USER's own account equity, not the bot's.
 */
const RISK_PER_TRADE_PCT = Number(process.env.RISK_PER_TRADE_PCT ?? '0.01')
const MAX_POSITION_PCT   = Number(process.env.MAX_POSITION_PCT   ?? '0.20')

export function sizePosition(equity: number, entry: number, stopLoss: number): number {
  const perShareRisk = Math.abs(entry - stopLoss)
  if (perShareRisk <= 0 || entry <= 0 || equity <= 0) return 0

  const riskUsd       = equity * RISK_PER_TRADE_PCT
  const qtyByRisk     = riskUsd / perShareRisk
  const qtyByExposure = (equity * MAX_POSITION_PCT) / entry

  return Math.floor(Math.min(qtyByRisk, qtyByExposure))
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd trading-dashboard && npx vitest run tests/lib/risk.test.ts`
Expected: PASS (6/6)

- [ ] **Step 5: Commit**

```bash
git add trading-dashboard/lib/risk.ts trading-dashboard/tests/lib/risk.test.ts
git commit -m "feat: add per-user position sizing helper"
```

---

### Task 2: Add `qty` to `ExecuteResponse`

**Files:**
- Modify: `trading-dashboard/types/trading.ts:98-102`

- [ ] **Step 1: Update the type**

Current:
```typescript
export interface ExecuteResponse {
  success:  boolean
  order_id: string
  message:  string
}
```

New:
```typescript
export interface ExecuteResponse {
  success:  boolean
  order_id: string
  qty:      number
  message:  string
}
```

- [ ] **Step 2: Commit**

```bash
git add trading-dashboard/types/trading.ts
git commit -m "feat: add qty field to ExecuteResponse"
```

(No standalone test — this is a type-only change, verified by Task 3's test
and `tsc` during build.)

---

### Task 3: Surface server error messages from `clientPost`

**Files:**
- Modify: `trading-dashboard/lib/api.ts:22-30`
- Test: `trading-dashboard/tests/lib/api.test.ts`

The execute route will return a JSON body with a `message` field on error
(e.g. 422 "balance too small"). Today `clientPost` discards the response body
on error and throws a generic `POST /path → 422`. Fix this so the real
message reaches the UI, and attach `.status` so callers can branch on it
(needed for Task 6's 409/422 handling).

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect, vi, afterEach } from 'vitest'

describe('lib/api clientPost error handling', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('throws an Error with the server message and status on failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ success: false, message: 'too small' }),
    }))

    const { api } = await import('@/lib/api')

    await expect(api.execute({
      recommendation_id: 'r1', ticker: 'AAPL', direction: 'LONG', qty: 1,
      entry: 100, stop_loss: 99, take_profit: 102,
    })).rejects.toMatchObject({ message: 'too small', status: 422 })
  })

  it('falls back to a generic message when the body has no message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => null,
    }))

    const { api } = await import('@/lib/api')

    await expect(api.execute({
      recommendation_id: 'r1', ticker: 'AAPL', direction: 'LONG', qty: 1,
      entry: 100, stop_loss: 99, take_profit: 102,
    })).rejects.toMatchObject({ status: 500 })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd trading-dashboard && npx vitest run tests/lib/api.test.ts`
Expected: FAIL — thrown error message is `POST /api/bot/execute → 422`, not `too small`; `.status` is undefined

- [ ] **Step 3: Update `clientPost`**

Current (`trading-dashboard/lib/api.ts:22-30`):
```typescript
async function clientPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`)
  return res.json()
}
```

New:
```typescript
async function clientPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const err = new Error(data?.message ?? `POST ${path} → ${res.status}`)
    Object.assign(err, { status: res.status })
    throw err
  }
  return data as T
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd trading-dashboard && npx vitest run tests/lib/api.test.ts`
Expected: PASS (2/2)

- [ ] **Step 5: Commit**

```bash
git add trading-dashboard/lib/api.ts trading-dashboard/tests/lib/api.test.ts
git commit -m "fix: surface server error messages and status from clientPost"
```

---

### Task 4: Size `qty` per-user in `/api/bot/execute`

**Files:**
- Modify: `trading-dashboard/app/api/bot/execute/route.ts`
- Test: `trading-dashboard/tests/api/execute.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/lib/session', () => ({
  getAlpacaCreds: vi.fn(),
}))
vi.mock('@/lib/alpaca', () => ({
  getAccount:         vi.fn(),
  submitBracketOrder: vi.fn(),
}))
vi.mock('@/lib/bot-api', () => ({
  botPost: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('next/cache', () => ({
  revalidatePath: vi.fn(),
}))

import { getAlpacaCreds } from '@/lib/session'
import { getAccount, submitBracketOrder } from '@/lib/alpaca'
import { POST } from '@/app/api/bot/execute/route'

const CREDS = { keyId: 'k', secret: 's', paper: true }

function makeRequest(body: unknown) {
  return new Request('http://localhost/api/bot/execute', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

const BASE_BODY = {
  recommendation_id: 'r-1',
  ticker:      'AAPL',
  direction:   'LONG' as const,
  qty:         999,        // bot-sized qty — must be ignored
  entry:       100,
  stop_loss:   98,
  take_profit: 106,
}

describe('POST /api/bot/execute', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getAlpacaCreds).mockResolvedValue(CREDS as any)
  })

  it('sizes qty from the executing user\'s own equity, ignoring body.qty', async () => {
    vi.mocked(getAccount).mockResolvedValue({ equity: '10000' } as any)
    vi.mocked(submitBracketOrder).mockResolvedValue({ id: 'order-1' } as any)

    const res  = await POST(makeRequest({ ...BASE_BODY, recommendation_id: 'r-sized' }))
    const data = await res.json()

    // equity=10000, risk 1% = $100, perShareRisk = 2 -> 50 by risk
    // exposure cap: 20% of 10000 / 100 = 20 -> exposure binds
    expect(data.qty).toBe(20)
    expect(submitBracketOrder).toHaveBeenCalledWith(CREDS, expect.objectContaining({ qty: 20 }))
  })

  it('returns 422 and submits nothing when sized qty is 0', async () => {
    vi.mocked(getAccount).mockResolvedValue({ equity: '10' } as any)

    const res  = await POST(makeRequest({ ...BASE_BODY, recommendation_id: 'r-too-small' }))
    const data = await res.json()

    expect(res.status).toBe(422)
    expect(data.success).toBe(false)
    expect(data.message).toMatch(/too small/i)
    expect(submitBracketOrder).not.toHaveBeenCalled()
  })

  it('returns 401 when the account cannot be fetched', async () => {
    vi.mocked(getAccount).mockRejectedValue(new Error('unauthorized'))

    const res  = await POST(makeRequest({ ...BASE_BODY, recommendation_id: 'r-acct-fail' }))
    const data = await res.json()

    expect(res.status).toBe(401)
    expect(data.success).toBe(false)
    expect(submitBracketOrder).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd trading-dashboard && npx vitest run tests/api/execute.test.ts`
Expected: FAIL — `data.qty` is `undefined`, no 422/401 paths exist yet, `getAccount` never called

- [ ] **Step 3: Update the route**

Current (`trading-dashboard/app/api/bot/execute/route.ts`):
```typescript
import { NextResponse }       from 'next/server'
import { revalidatePath }      from 'next/cache'
import { submitBracketOrder } from '@/lib/alpaca'
import { getAlpacaCreds }     from '@/lib/session'
import { botPost }            from '@/lib/bot-api'
import type { ExecuteRequest, ExecuteResponse } from '@/types/trading'

// ── Idempotency guard ──────────────────────────────────────────────────────
// Reject the same recommendation_id if it arrives again within 30 seconds.
// Prevents double-executions from rapid clicks or network retries.
const _recentIds  = new Map<string, number>() // rec_id -> timestamp ms
const _DEDUP_MS   = 30_000

function _isDuplicate(recId: string | undefined): boolean {
  if (!recId) return false
  const now = Date.now()
  for (const [id, ts] of _recentIds) {
    if (now - ts > _DEDUP_MS) _recentIds.delete(id)
  }
  if (_recentIds.has(recId)) return true
  _recentIds.set(recId, now)
  return false
}

export async function POST(req: Request) {
  const creds = await getAlpacaCreds()
  if (!creds) {
    return NextResponse.json(
      { success: false, order_id: '', message: 'Unauthorized' },
      { status: 401 },
    )
  }

  let body: ExecuteRequest
  try {
    body = await req.json()
  } catch {
    return NextResponse.json(
      { success: false, order_id: '', message: 'Invalid request body' },
      { status: 400 },
    )
  }

  // Idempotency check — 409 if same rec_id arrives within 30s
  if (_isDuplicate(body.recommendation_id)) {
    return NextResponse.json(
      { success: false, order_id: '', message: `Duplicate: '${body.recommendation_id}' already executed within 30s` },
      { status: 409 },
    )
  }

  const { ticker, direction, qty, stop_loss, take_profit } = body

  // -- 1. Submit to the signed-in user's Alpaca account
  let orderId = `PAPER-${Date.now().toString(36).toUpperCase()}`
  let message = ''
  let alpacaSuccess = false

  try {
    const alpacaOrder = await submitBracketOrder(creds, {
      symbol:      ticker,
      side:        direction === 'LONG' ? 'buy' : 'sell',
      qty,
      stop_loss,
      take_profit,
    })
    orderId       = alpacaOrder.id
    message       = `${direction} ${qty}x ${ticker} submitted to Alpaca ${creds.paper ? 'Paper' : 'Live'} (order ${orderId})`
    alpacaSuccess = true
  } catch (err: any) {
    message = `${direction} ${qty}x ${ticker} recorded locally (Alpaca: ${err.message})`
  }

  // -- 2. Notify bot server (best-effort) — include resolved order_id and score
  botPost('/api/execute', {
    ...body,
    order_id: orderId,
    score:    body.composite_score ?? null,
  }).catch(() => {})

  // -- 3. Invalidate dashboard cache so positions refresh on next load
  revalidatePath('/')
  revalidatePath('/history')
  revalidatePath('/pnl')

  // -- 4. Always return success
  return NextResponse.json({
    success:  true,
    order_id: orderId,
    message,
    alpaca:   alpacaSuccess,
  } as ExecuteResponse & { alpaca: boolean })
}
```

New:
```typescript
import { NextResponse }       from 'next/server'
import { revalidatePath }      from 'next/cache'
import { submitBracketOrder, getAccount } from '@/lib/alpaca'
import { getAlpacaCreds }     from '@/lib/session'
import { botPost }            from '@/lib/bot-api'
import { sizePosition }       from '@/lib/risk'
import type { ExecuteRequest, ExecuteResponse } from '@/types/trading'

// ── Idempotency guard ──────────────────────────────────────────────────────
// Reject the same recommendation_id if it arrives again within 30 seconds.
// Prevents double-executions from rapid clicks or network retries.
const _recentIds  = new Map<string, number>() // rec_id -> timestamp ms
const _DEDUP_MS   = 30_000

function _isDuplicate(recId: string | undefined): boolean {
  if (!recId) return false
  const now = Date.now()
  for (const [id, ts] of _recentIds) {
    if (now - ts > _DEDUP_MS) _recentIds.delete(id)
  }
  if (_recentIds.has(recId)) return true
  _recentIds.set(recId, now)
  return false
}

export async function POST(req: Request) {
  const creds = await getAlpacaCreds()
  if (!creds) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'Unauthorized' },
      { status: 401 },
    )
  }

  let body: ExecuteRequest
  try {
    body = await req.json()
  } catch {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'Invalid request body' },
      { status: 400 },
    )
  }

  // Idempotency check — 409 if same rec_id arrives within 30s
  if (_isDuplicate(body.recommendation_id)) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: `Duplicate: '${body.recommendation_id}' already executed within 30s` },
      { status: 409 },
    )
  }

  const { ticker, direction, entry, stop_loss, take_profit } = body

  // -- 0. Size the position to the EXECUTING USER's own account equity
  let equity: number
  try {
    const account = await getAccount(creds)
    equity = parseFloat(account.equity)
  } catch (err: any) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: `Could not fetch your Alpaca account: ${err.message}` },
      { status: 401 },
    )
  }

  const qty = sizePosition(equity, entry, stop_loss)
  if (qty <= 0) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'Your account balance is too small to size this trade within risk limits' },
      { status: 422 },
    )
  }

  // -- 1. Submit to the signed-in user's Alpaca account
  let orderId = `PAPER-${Date.now().toString(36).toUpperCase()}`
  let message = ''
  let alpacaSuccess = false

  try {
    const alpacaOrder = await submitBracketOrder(creds, {
      symbol:      ticker,
      side:        direction === 'LONG' ? 'buy' : 'sell',
      qty,
      stop_loss,
      take_profit,
    })
    orderId       = alpacaOrder.id
    message       = `${direction} ${qty}x ${ticker} submitted to Alpaca ${creds.paper ? 'Paper' : 'Live'} (order ${orderId})`
    alpacaSuccess = true
  } catch (err: any) {
    message = `${direction} ${qty}x ${ticker} recorded locally (Alpaca: ${err.message})`
  }

  // -- 2. Notify bot server (best-effort) — include resolved order_id, qty, and score
  botPost('/api/execute', {
    ...body,
    qty,
    order_id: orderId,
    score:    body.composite_score ?? null,
  }).catch(() => {})

  // -- 3. Invalidate dashboard cache so positions refresh on next load
  revalidatePath('/')
  revalidatePath('/history')
  revalidatePath('/pnl')

  // -- 4. Always return success
  return NextResponse.json({
    success:  true,
    order_id: orderId,
    qty,
    message,
    alpaca:   alpacaSuccess,
  } as ExecuteResponse & { alpaca: boolean })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd trading-dashboard && npx vitest run tests/api/execute.test.ts`
Expected: PASS (3/3)

- [ ] **Step 5: Commit**

```bash
git add trading-dashboard/app/api/bot/execute/route.ts trading-dashboard/tests/api/execute.test.ts
git commit -m "feat: size order qty from the executing user's own Alpaca equity"
```

---

### Task 5: `ConfirmModal.tsx` — use returned `qty`, add disclaimer

**Files:**
- Modify: `trading-dashboard/components/trades/ConfirmModal.tsx`

No automated test (UI-only text/string change covered by manual smoke test in
Task 7). Existing `handleExecute` already has a try/catch with
`toast.error('Execution failed', { description: err.message })` — with
Task 3's fix, `err.message` will now be the server's 422 message
("Your account balance is too small...") instead of a generic string, so no
changes are needed to the catch block itself.

- [ ] **Step 1: Use `res.qty` in the success toast**

Current (`trading-dashboard/components/trades/ConfirmModal.tsx:36-40`):
```typescript
      setConfirmed(true)
      toast.success(`Trade executed: ${trade.direction} ${trade.risk.qty}x ${trade.ticker}`, {
        description: `Order ID: ${res.order_id}`,
      })
```

New:
```typescript
      setConfirmed(true)
      toast.success(`Trade executed: ${trade.direction} ${res.qty}x ${trade.ticker}`, {
        description: `Order ID: ${res.order_id}`,
      })
```

- [ ] **Step 2: Add a disclaimer near the Quantity field**

Current (`trading-dashboard/components/trades/ConfirmModal.tsx:103-122`):
```tsx
          <div className="grid grid-cols-2 gap-2 text-center text-xs sm:grid-cols-4">
            <div>
              <p className="text-muted">Quantity</p>
              <p className="font-mono font-semibold text-primary mt-0.5">{trade.risk.qty} shares</p>
            </div>
            <div>
              <p className="text-muted">Total Cost</p>
              <p className="font-mono font-semibold text-primary mt-0.5">
                ${totalCost.toLocaleString('en-US', { maximumFractionDigits: 0 })}
              </p>
            </div>
            <div>
              <p className="text-muted">R/R Ratio</p>
              <p className="font-mono font-semibold text-brand-cyan mt-0.5">{trade.risk.risk_reward.toFixed(2)}x</p>
            </div>
            <div>
              <p className="text-muted">Dollar Risk</p>
              <p className="font-mono font-semibold text-bear mt-0.5">${trade.risk.dollar_risk.toFixed(0)}</p>
            </div>
          </div>
```

New:
```tsx
          <div className="grid grid-cols-2 gap-2 text-center text-xs sm:grid-cols-4">
            <div>
              <p className="text-muted">Quantity</p>
              <p className="font-mono font-semibold text-primary mt-0.5">{trade.risk.qty} shares</p>
            </div>
            <div>
              <p className="text-muted">Total Cost</p>
              <p className="font-mono font-semibold text-primary mt-0.5">
                ${totalCost.toLocaleString('en-US', { maximumFractionDigits: 0 })}
              </p>
            </div>
            <div>
              <p className="text-muted">R/R Ratio</p>
              <p className="font-mono font-semibold text-brand-cyan mt-0.5">{trade.risk.risk_reward.toFixed(2)}x</p>
            </div>
            <div>
              <p className="text-muted">Dollar Risk</p>
              <p className="font-mono font-semibold text-bear mt-0.5">${trade.risk.dollar_risk.toFixed(0)}</p>
            </div>
          </div>

          <p className="text-center text-[10px] text-muted">
            Quantity will be resized to your account balance at execution.
          </p>
```

- [ ] **Step 3: Commit**

```bash
git add trading-dashboard/components/trades/ConfirmModal.tsx
git commit -m "feat: show server-sized qty and disclaimer in execute confirmation"
```

---

### Task 6: `app/trades/page.tsx` `handleBuyAll` — use returned `qty`, handle 422

**Files:**
- Modify: `trading-dashboard/app/trades/page.tsx:138-187`

No automated test — this is a client-side loop over a network call; covered
by manual smoke test in Task 7. Relies on Task 3's `err.status` addition.

- [ ] **Step 1: Update `handleBuyAll`**

Current (`trading-dashboard/app/trades/page.tsx:138-187`):
```typescript
  async function handleBuyAll() {
    if (!active.length || buyingAll) return
    setBuyingAll(true)
    let succeeded = 0
    let failed    = 0
    for (const trade of active) {
      try {
        await api.execute({
          recommendation_id: trade.id,
          ticker:          trade.ticker,
          direction:       trade.direction,
          qty:             trade.risk.qty,
          entry:           trade.risk.entry,
          stop_loss:       trade.risk.stop_loss,
          take_profit:     trade.risk.take_profit,
          composite_score: trade.composite_score,
        })
        const newIds = new Set(executedIds).add(tradeKey(trade))
        setExecutedIds(newIds)
        saveIds(newIds)
        const newRecs = [trade, ...loadExecRecs()]
        saveExecRecs(newRecs)
        setExecutedRecs(newRecs)
        succeeded++
        toast.success(`${trade.direction} ${trade.ticker}`, {
          description: `${trade.risk.qty} shares @ ${trade.risk.entry}`,
        })
      } catch (err: any) {
        const msg: string = err?.message ?? ''
        if (msg.includes('409')) {
          // Idempotency dedup — already submitted within 30s, mark as executed
          const newIds = new Set(executedIds).add(tradeKey(trade))
          setExecutedIds(newIds)
          saveIds(newIds)
          toast.info(`${trade.ticker} already submitted`, { description: 'Skipped duplicate within 30s' })
        } else {
          failed++
          toast.error(`Failed: ${trade.ticker}`, { description: msg || undefined })
        }
      }
    }
    setBuyingAll(false)
    setShowExecuted(true)
    if (succeeded > 0) {
      toast.success(`Bought all — ${succeeded} trade${succeeded > 1 ? 's' : ''} submitted`, {
        description: failed > 0 ? `${failed} failed` : undefined,
      })
      router.push('/')
    }
  }
```

New:
```typescript
  async function handleBuyAll() {
    if (!active.length || buyingAll) return
    setBuyingAll(true)
    let succeeded = 0
    let failed    = 0
    for (const trade of active) {
      try {
        const res = await api.execute({
          recommendation_id: trade.id,
          ticker:          trade.ticker,
          direction:       trade.direction,
          qty:             trade.risk.qty,
          entry:           trade.risk.entry,
          stop_loss:       trade.risk.stop_loss,
          take_profit:     trade.risk.take_profit,
          composite_score: trade.composite_score,
        })
        const newIds = new Set(executedIds).add(tradeKey(trade))
        setExecutedIds(newIds)
        saveIds(newIds)
        const newRecs = [trade, ...loadExecRecs()]
        saveExecRecs(newRecs)
        setExecutedRecs(newRecs)
        succeeded++
        toast.success(`${trade.direction} ${trade.ticker}`, {
          description: `${res.qty} shares @ ${trade.risk.entry}`,
        })
      } catch (err: any) {
        if (err?.status === 409) {
          // Idempotency dedup — already submitted within 30s, mark as executed
          const newIds = new Set(executedIds).add(tradeKey(trade))
          setExecutedIds(newIds)
          saveIds(newIds)
          toast.info(`${trade.ticker} already submitted`, { description: 'Skipped duplicate within 30s' })
        } else if (err?.status === 422) {
          // Account too small to size this trade — skip, not a hard failure
          failed++
          toast.error(`${trade.ticker} skipped`, { description: err.message })
        } else {
          failed++
          toast.error(`Failed: ${trade.ticker}`, { description: err?.message || undefined })
        }
      }
    }
    setBuyingAll(false)
    setShowExecuted(true)
    if (succeeded > 0) {
      toast.success(`Bought all — ${succeeded} trade${succeeded > 1 ? 's' : ''} submitted`, {
        description: failed > 0 ? `${failed} failed` : undefined,
      })
      router.push('/')
    }
  }
```

- [ ] **Step 2: Commit**

```bash
git add trading-dashboard/app/trades/page.tsx
git commit -m "feat: use server-sized qty in Buy All and handle undersized-account skips"
```

---

### Task 7: Full test run + manual smoke test

**Files:** none (verification only)

- [ ] **Step 1: Run the full dashboard test suite**

Run: `cd trading-dashboard && npm test`
Expected: all suites pass, including the 3 new files from Tasks 1, 3, 4

- [ ] **Step 2: Run the bot test suite (per CLAUDE.md convention)**

Run: `cd trading_bot && python -m pytest tests -q`
Expected: 25/25 pass (no Python files were touched — this just confirms no
regressions)

- [ ] **Step 3: Manual smoke test against Railway + a real paper account**

1. Log in to the dashboard as a user with a small paper-account balance.
2. Open a trade recommendation, click "Execute".
3. Confirm the toast shows a `qty` consistent with
   `sizePosition(yourEquity, entry, stop_loss)` — NOT the bot's displayed
   `risk.qty`.
4. Check the order in Alpaca's paper dashboard — submitted qty must match.
5. If the account is small enough that `sizePosition(...)` returns 0, confirm
   the modal shows the "too small to size this trade" error and no order is
   submitted.

- [ ] **Step 4: Commit (if any fixes were needed)**

Only if Steps 1-3 surfaced issues requiring code changes:
```bash
git add -A
git commit -m "fix: address issues found in per-user sizing smoke test"
```

---

### Task 8: Vercel environment variables (user action — no code)

**Files:** none

- [ ] Add to the Vercel project's environment variables (Production +
  Preview), matching `trading_bot/.env`'s risk config:
  ```
  RISK_PER_TRADE_PCT=0.01
  MAX_POSITION_PCT=0.20
  ```
  These are read by `lib/risk.ts` (Task 1) with the same defaults baked in,
  so the app works even before these are set — but set them for parity with
  the bot's config and so future changes to one can be mirrored to the other.

---

## Self-Review Against Spec

- **A. Risk sizing helper** — Task 1, matches spec exactly (same formula,
  same env var names/defaults).
- **B. Config (env vars)** — Task 8.
- **C. Route changes** — Task 4: `getAccount(creds)` called, `qty` recomputed
  via `sizePosition`, `qty <= 0` → 422 with the exact spec message, computed
  `qty` used for `submitBracketOrder`/`botPost`/response, `qty` included in
  response. Account-fetch failure → 401 (per spec: "don't fall back to
  `body.qty`").
- **D. Type changes** — Task 2.
- **E. UI changes** — Task 5 (ConfirmModal success toast + disclaimer) and
  Task 6 (handleBuyAll toast + 422 handling). Task 3 additionally fixes
  `clientPost` so the 422/error messages from the route actually reach these
  toasts — this was implied by spec section E ("verify the message surfaces
  correctly") but required a small upstream fix not explicitly listed in the
  spec; included here as it's necessary for E to work at all.
- **Out of scope** — No `trading_bot/` (Python) changes, no DB schema
  changes, no recommendation-generation changes. Confirmed: none of the tasks
  above touch `trading_bot/`.
- **Testing** — Task 7 covers the manual qty-match and qty<=0 checks from the
  spec's Testing section, plus the bot pytest run.
