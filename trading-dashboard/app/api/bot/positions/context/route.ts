/**
 * GET /api/bot/positions/context
 *
 * Returns open-trade TP/SL context from the bot server.
 * PositionsTable reads this instead of localStorage so context
 * persists across devices and survives page refreshes.
 *
 * Returns [] if bot server is unreachable (graceful degradation).
 */
import { NextResponse } from 'next/server'
import { botGet }       from '@/lib/bot-api'

export interface PositionContext {
  ticker:          string
  direction:       'LONG' | 'SHORT'
  entry:           number
  stop_loss:       number | null
  take_profit:     number | null
  qty:             number
  composite_score: number | null
  order_id:        string | null
  executed_at:     string | null
}

export async function GET() {
  try {
    const data = await botGet('/api/open') as PositionContext[]
    return NextResponse.json(data)
  } catch {
    // Bot server offline — return empty array; UI falls back to localStorage
    return NextResponse.json([])
  }
}
