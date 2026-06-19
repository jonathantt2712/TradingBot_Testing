import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { getFills, tradesFromFills } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import type { TradeRecord } from '@/types/trading'

/**
 * Returns completed round-trip trades with real P&L.
 * Priority: bot's persisted records (have exact P&L) → Alpaca fills FIFO-matched
 * (accurate P&L from actual executions) → empty list.
 * Never falls back to demo data — callers decide what to show when empty.
 */
export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const [botResult, fillsResult] = await Promise.allSettled([
      botGet<TradeRecord[]>('/api/history'),
      getFills(creds, 500),
    ])

    // Bot trades have real P&L — use as primary source
    const botTrades: TradeRecord[] = botResult.status === 'fulfilled'
      ? botResult.value.map(t => ({
          ...t,
          opened_at: t.opened_at ?? (t as any).executed_at ?? '',
        }))
      : []

    // Fill gaps with FIFO-matched Alpaca fills (complete round-trip P&L)
    const fillTrades: TradeRecord[] = fillsResult.status === 'fulfilled'
      ? tradesFromFills(fillsResult.value)
      : []

    // Deduplicate: prefer bot records (they have more detail)
    const botKeys = new Set(botTrades.map(t => `${t.ticker}-${t.opened_at?.slice(0, 10)}`))
    const fillGaps = fillTrades.filter(t => !botKeys.has(`${t.ticker}-${t.opened_at?.slice(0, 10)}`))

    const merged = [...botTrades, ...fillGaps]
      .sort((a, b) => (b.opened_at ?? '').localeCompare(a.opened_at ?? ''))

    return NextResponse.json(merged)
  } catch {
    return NextResponse.json([])
  }
}
