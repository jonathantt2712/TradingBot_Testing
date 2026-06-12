import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { getAccount } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoStats } from '@/lib/api'
import type { PortfolioStats } from '@/types/trading'

export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    // Pull stats from bot + enrich with live Alpaca account data
    const [botStats, account] = await Promise.allSettled([
      botGet<PortfolioStats>('/api/stats'),
      getAccount(creds),
    ])

    const stats: PortfolioStats = botStats.status === 'fulfilled'
      ? botStats.value
      : demoStats()

    // Override P&L with live Alpaca equity if available
    if (account.status === 'fulfilled') {
      const acc = account.value
      const totalPnl = parseFloat(acc.unrealized_pl) + parseFloat(acc.realized_pl ?? '0')
      const todayPnl = parseFloat(acc.equity) - parseFloat(acc.last_equity)
      if (!isNaN(totalPnl)) stats.total_pnl = +totalPnl.toFixed(2)
      if (!isNaN(todayPnl)) stats.today_pnl = +todayPnl.toFixed(2)
      stats.open_positions = 0 // will be filled by positions endpoint
    }

    return NextResponse.json(stats)
  } catch {
    return NextResponse.json(demoStats())
  }
}
