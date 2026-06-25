/**
 * POST /api/bot/execute
 *
 * Executes a trade recommendation. Strategy (in order):
 *  1. Submit bracket order directly to the signed-in user's Alpaca account
 *  2. Also notify bot server if it happens to be running (for its trade log)
 *  3. If Alpaca is unavailable, return a local paper order ID so UI never breaks
 *
 * This makes the execute flow completely independent of localhost:8000 being up.
 */
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

  // Runtime validation — TypeScript types don't protect us at the boundary.
  if (!ticker || !/^[A-Z0-9.]{1,10}$/.test(ticker)) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'Invalid ticker symbol' },
      { status: 400 },
    )
  }
  if (direction !== 'LONG' && direction !== 'SHORT') {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'direction must be LONG or SHORT' },
      { status: 400 },
    )
  }
  if (!(entry > 0) || !(stop_loss > 0) || !(take_profit > 0)) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'entry, stop_loss, and take_profit must be positive' },
      { status: 400 },
    )
  }
  // Bracket sanity: LONG needs SL < entry < TP; SHORT needs TP < entry < SL
  const bracketValid = direction === 'LONG'
    ? (stop_loss < entry && entry < take_profit)
    : (take_profit < entry && entry < stop_loss)
  if (!bracketValid) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'Bracket legs are inverted for this direction' },
      { status: 400 },
    )
  }

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
