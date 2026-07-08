import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { getAccount, getOrders, getPortfolioHistory } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoStats } from '@/lib/api'
import type { PortfolioStats } from '@/types/trading'

export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const [botStats, account, history, alpacaOrders] = await Promise.allSettled([
      botGet<PortfolioStats>('/api/stats'),
      getAccount(creds),
      getPortfolioHistory(creds, '1A', '1D'),
      getOrders(creds, 'closed', 200),
    ])

    const stats: PortfolioStats = botStats.status === 'fulfilled'
      ? botStats.value
      : demoStats()

    if (account.status === 'fulfilled') {
      const acc = account.value
      const todayPnl = parseFloat(acc.equity) - parseFloat(acc.last_equity)
      if (!isNaN(todayPnl)) stats.today_pnl = +todayPnl.toFixed(2)

      if (history.status === 'fulfilled') {
        const base = history.value.base_value
        const totalPnl = parseFloat(acc.equity) - base
        if (base > 0 && !isNaN(totalPnl)) stats.total_pnl = +totalPnl.toFixed(2)
      }
    }

    // If bot reports 0% win rate, recompute from Alpaca orders directly.
    // Bot marks manually-closed trades as 'cancelled' (no pnl) until its
    // next reconciliation cycle, so its win_rate can be stale.
    if ((stats.win_rate === 0 || stats.total_trades === 0) && alpacaOrders.status === 'fulfilled') {
      const orders   = alpacaOrders.value
      const sellFills = orders.filter(o =>
        o.side === 'sell' && parseFloat(o.filled_qty) > 0 && o.filled_avg_price
      )
      const buyFills  = orders.filter(o =>
        o.side === 'buy' && parseFloat(o.filled_qty) > 0 && o.filled_avg_price
      )

      // Pair buy→sell for LONG trades to compute realized pnl
      const pairedPnls: number[] = []
      for (const buy of buyFills) {
        const sell = sellFills.find(s =>
          s.symbol === buy.symbol && (s.filled_at ?? '') > (buy.filled_at ?? '')
        )
        if (!sell?.filled_avg_price) continue
        const entry = parseFloat(buy.filled_avg_price ?? '0')
        const exit  = parseFloat(sell.filled_avg_price)
        const qty   = parseFloat(buy.filled_qty)
        pairedPnls.push((exit - entry) * qty)
      }

      if (pairedPnls.length > 0) {
        const wins = pairedPnls.filter(p => p > 0).length
        stats.win_rate     = +(wins / pairedPnls.length * 100).toFixed(1)
        stats.total_trades = pairedPnls.length
      }
    }

    return NextResponse.json(stats)
  } catch {
    return NextResponse.json(demoStats())
  }
}
