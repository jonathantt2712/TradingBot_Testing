import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { getOrders } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoHistory } from '@/lib/api'
import type { TradeRecord } from '@/types/trading'

/** Merge bot history with Alpaca orders. Handles the executed_at→opened_at
 *  field rename and retroactively computes pnl for manually-closed trades. */
export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    // 1. Bot trades — bot stores executed_at, not opened_at
    const rawBot = await botGet<any[]>('/api/history').catch(() => [])
    const botTrades: TradeRecord[] = rawBot.map((t: any) => ({
      ...t,
      opened_at: t.opened_at ?? t.executed_at ?? '',
    }))

    // 2. Alpaca closed orders (fills) — used to enrich cancelled trades + fallback
    const alpacaOrders = await getOrders(creds, 'closed', 200).catch(() => [])

    // Sell fills sorted chronologically (earliest first) for LONG exits
    const sellFills = alpacaOrders
      .filter(o => o.side === 'sell' && parseFloat(o.filled_qty) > 0 && o.filled_avg_price && o.filled_at)
      .sort((a, b) => (a.filled_at ?? '').localeCompare(b.filled_at ?? ''))

    // 3. Enrich cancelled bot trades that have no pnl with Alpaca fill price
    const enriched: TradeRecord[] = botTrades.map(t => {
      if (t.pnl != null || t.status !== 'cancelled' || !t.entry || !t.qty) return t
      const match = sellFills.find(o =>
        o.symbol === t.ticker && (o.filled_at ?? '') > (t.opened_at || '')
      )
      if (!match?.filled_avg_price) return t
      const exit    = parseFloat(match.filled_avg_price)
      const mult    = t.direction === 'LONG' ? 1 : -1
      const pnl     = +(mult * (exit - t.entry) * t.qty).toFixed(2)
      const pnl_pct = +(mult * (exit - t.entry) / t.entry * 100).toFixed(2)
      return { ...t, exit, pnl, pnl_pct, status: 'closed' as const, closed_at: match.filled_at ?? t.closed_at }
    })

    // 4. Fallback: Alpaca BUY fills not already represented in bot history
    const botOrderIds = new Set(enriched.map(t => t.order_id).filter(Boolean))
    const fromAlpaca: TradeRecord[] = alpacaOrders
      .filter(o =>
        o.side === 'buy' &&
        parseFloat(o.filled_qty) > 0 &&
        o.filled_avg_price &&
        !botOrderIds.has(o.id)
      )
      .map(o => ({
        id:        o.id,
        ticker:    o.symbol,
        direction: 'LONG' as const,
        entry:     parseFloat(o.filled_avg_price ?? '0'),
        exit:      null,
        qty:       parseInt(o.filled_qty),
        pnl:       null,
        pnl_pct:   null,
        opened_at: o.created_at ?? o.filled_at ?? '',
        closed_at: null,
        duration:  null,
        status:    'open' as const,
        order_id:  o.id,
      }))

    const merged = [...enriched, ...fromAlpaca]
      .sort((a, b) => (b.opened_at || '').localeCompare(a.opened_at || ''))

    return NextResponse.json(merged.length ? merged : demoHistory())
  } catch {
    return NextResponse.json(demoHistory())
  }
}
