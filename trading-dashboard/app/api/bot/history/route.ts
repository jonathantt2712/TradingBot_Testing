import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { getOrders } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoHistory } from '@/lib/api'
import type { TradeRecord } from '@/types/trading'

/** Best available timestamp for an Alpaca order (for sorting / comparisons). */
function orderTs(o: { filled_at?: string | null; created_at?: string }): string {
  return o.filled_at ?? o.created_at ?? ''
}

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

    // 2. Alpaca closed orders (fills)
    const alpacaOrders = await getOrders(creds, 'closed', 200).catch(() => [])

    console.log('[history] bot trades:', botTrades.length, 'alpaca orders:', alpacaOrders.length)

    // Sell fills — no filled_at requirement; use created_at as fallback
    const sellFills = alpacaOrders
      .filter(o => o.side === 'sell' && parseFloat(o.filled_qty) > 0 && o.filled_avg_price)
      .sort((a, b) => orderTs(a).localeCompare(orderTs(b)))

    console.log('[history] sell fills:', sellFills.length,
      sellFills.slice(0, 3).map(o => ({ sym: o.symbol, price: o.filled_avg_price, ts: orderTs(o) })))

    // 3. Enrich cancelled bot trades that have no pnl with Alpaca fill price
    const enriched: TradeRecord[] = botTrades.map(t => {
      if (t.pnl != null || t.status !== 'cancelled' || !t.entry || !t.qty) return t
      const match = sellFills.find(o =>
        o.symbol === t.ticker && orderTs(o) > (t.opened_at || '')
      )
      if (!match?.filled_avg_price) return t
      const exit    = parseFloat(match.filled_avg_price)
      const mult    = t.direction === 'LONG' ? 1 : -1
      const pnl     = +(mult * (exit - t.entry) * t.qty).toFixed(2)
      const pnl_pct = +(mult * (exit - t.entry) / t.entry * 100).toFixed(2)
      return { ...t, exit, pnl, pnl_pct, status: 'closed' as const, closed_at: orderTs(match) || t.closed_at }
    })

    // 4. Fallback: Alpaca BUY fills not already in bot history, paired with sell fills
    const botOrderIds = new Set(enriched.map(t => t.order_id).filter(Boolean))
    const buyFills = alpacaOrders.filter(o =>
      o.side === 'buy' &&
      parseFloat(o.filled_qty) > 0 &&
      o.filled_avg_price &&
      !botOrderIds.has(o.id)
    )

    console.log('[history] buy fills (not in bot):', buyFills.length,
      buyFills.slice(0, 3).map(o => ({ sym: o.symbol, price: o.filled_avg_price, ts: orderTs(o) })))

    const fromAlpaca: TradeRecord[] = buyFills.map(o => {
      const entry    = parseFloat(o.filled_avg_price ?? '0')
      const qty      = parseFloat(o.filled_qty)
      const openedAt = orderTs(o)
      const sellMatch = sellFills.find(s =>
        s.symbol === o.symbol && orderTs(s) > openedAt
      )
      if (sellMatch?.filled_avg_price) {
        const exit    = parseFloat(sellMatch.filled_avg_price)
        const pnl     = +((exit - entry) * qty).toFixed(2)
        const pnl_pct = +((exit - entry) / entry * 100).toFixed(2)
        return {
          id: o.id, ticker: o.symbol, direction: 'LONG' as const,
          entry, exit, qty: Math.round(qty), pnl, pnl_pct,
          opened_at: openedAt, closed_at: orderTs(sellMatch) || null,
          duration: null, status: 'closed' as const, order_id: o.id,
        }
      }
      return {
        id: o.id, ticker: o.symbol, direction: 'LONG' as const,
        entry, exit: null, qty: Math.round(qty), pnl: null, pnl_pct: null,
        opened_at: openedAt, closed_at: null,
        duration: null, status: 'open' as const, order_id: o.id,
      }
    })

    const merged = [...enriched, ...fromAlpaca]
      .sort((a, b) => (b.opened_at || '').localeCompare(a.opened_at || ''))

    console.log('[history] merged:', merged.length,
      'with pnl:', merged.filter(t => t.pnl != null).length)

    return NextResponse.json(merged.length ? merged : demoHistory())
  } catch (err) {
    console.error('[history] fatal:', err)
    return NextResponse.json(demoHistory())
  }
}
