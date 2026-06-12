import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { getOrders } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoHistory } from '@/lib/api'
import type { TradeRecord } from '@/types/trading'

/** Merge bot history with real Alpaca orders for full picture. */
export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    // 1. Bot's persisted trade records
    const botTrades = await botGet<TradeRecord[]>('/api/history').catch(() => [])

    // 2. Real closed orders from Alpaca (fills broker gaps)
    const alpacaOrders = await getOrders(creds, 'closed', 100).catch(() => [])
    const fromAlpaca: TradeRecord[] = alpacaOrders
      .filter(o => o.filled_qty && parseFloat(o.filled_qty) > 0)
      .map(o => ({
        id:        o.id,
        ticker:    o.symbol,
        direction: o.side === 'buy' ? 'LONG' : 'SHORT',
        entry:     parseFloat(o.filled_avg_price ?? '0'),
        exit:      null,
        qty:       parseInt(o.filled_qty),
        pnl:       null,
        pnl_pct:   null,
        opened_at: o.created_at,
        closed_at: o.filled_at,
        duration:  null,
        status:    'closed',
        order_id:  o.id,
      } as TradeRecord))

    // Merge — bot records take precedence (they have P&L calc)
    const botIds = new Set(botTrades.map(t => t.order_id).filter(Boolean))
    const merged = [
      ...botTrades,
      ...fromAlpaca.filter(t => !botIds.has(t.id)),
    ].sort((a, b) => b.opened_at.localeCompare(a.opened_at))

    return NextResponse.json(merged.length ? merged : demoHistory())
  } catch {
    return NextResponse.json(demoHistory())
  }
}
