/**
 * POST /api/bot/execute
 *
 * Executes a trade recommendation. Strategy (in order):
 *  1. Submit bracket order directly to Alpaca Paper API (no bot server needed)
 *  2. Also notify bot server if it happens to be running (for its trade log)
 *  3. If Alpaca is unavailable, return a local paper order ID so UI never breaks
 *
 * This makes the execute flow completely independent of localhost:8000 being up.
 */
import { NextResponse }       from 'next/server'
import { revalidatePath }      from 'next/cache'
import { submitBracketOrder } from '@/lib/alpaca'
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

  // -- 1. Submit to Alpaca Paper
  let orderId = `PAPER-${Date.now().toString(36).toUpperCase()}`
  let message = ''
  let alpacaSuccess = false

  try {
    const alpacaOrder = await submitBracketOrder({
      symbol:      ticker,
      side:        direction === 'LONG' ? 'buy' : 'sell',
      qty,
      stop_loss,
      take_profit,
    })
    orderId       = alpacaOrder.id
    message       = `${direction} ${qty}x ${ticker} submitted to Alpaca Paper (order ${orderId})`
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
