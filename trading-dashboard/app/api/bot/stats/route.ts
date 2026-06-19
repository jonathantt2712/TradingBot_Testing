import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { getAccount, getPortfolioHistory } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoStats } from '@/lib/api'
import type { PortfolioStats, TradeRecord } from '@/types/trading'

function winRateFromHistory(trades: TradeRecord[]): { win_rate: number; total_trades: number } | null {
  const closed = trades.filter(t => t.status === 'closed' && t.pnl != null)
  if (closed.length === 0) return null
  const wins = closed.filter(t => (t.pnl ?? 0) > 0).length
  return {
    win_rate:     +(wins / closed.length * 100).toFixed(1),
    total_trades: closed.length,
  }
}

export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const [botStats, account, portfolioHistory, tradeHistory] = await Promise.allSettled([
      botGet<PortfolioStats>('/api/stats'),
      getAccount(creds),
      getPortfolioHistory(creds, '1A', '1D'),
      botGet<TradeRecord[]>('/api/history'),
    ])

    const stats: PortfolioStats = botStats.status === 'fulfilled'
      ? botStats.value
      : { ...demoStats(), win_rate: 0, total_trades: 0, total_pnl: 0, today_pnl: 0, sharpe_ratio: 0 }

    // Compute win rate from history trades (same source as History page).
    // This works even when the bot's local HISTORY_FILE is empty on Railway,
    // because the history route merges bot records with real Alpaca orders.
    if (tradeHistory.status === 'fulfilled') {
      const computed = winRateFromHistory(tradeHistory.value)
      if (computed) {
        stats.win_rate     = computed.win_rate
        stats.total_trades = computed.total_trades
      }
    }

    if (account.status === 'fulfilled') {
      const acc = account.value
      const todayPnl = parseFloat(acc.equity) - parseFloat(acc.last_equity)
      if (!isNaN(todayPnl)) stats.today_pnl = +todayPnl.toFixed(2)

      if (portfolioHistory.status === 'fulfilled') {
        const base     = portfolioHistory.value.base_value
        const totalPnl = parseFloat(acc.equity) - base
        if (base > 0 && !isNaN(totalPnl)) stats.total_pnl = +totalPnl.toFixed(2)
      }
    }

    return NextResponse.json(stats)
  } catch {
    return NextResponse.json({ ...demoStats(), win_rate: 0, total_trades: 0, total_pnl: 0, today_pnl: 0, sharpe_ratio: 0 })
  }
}
