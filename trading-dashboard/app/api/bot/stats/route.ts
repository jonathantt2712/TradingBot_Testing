import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { getAccount, getPortfolioHistory } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoStats } from '@/lib/api'
import type { PortfolioStats } from '@/types/trading'

export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const [botStats, account, history] = await Promise.allSettled([
      botGet<PortfolioStats>('/api/stats'),
      getAccount(creds),
      getPortfolioHistory(creds, '1A', '1D'),
    ])

    // When the bot is unreachable use demoStats only for the object *shape*,
    // not the values — zero out metrics so fake demo numbers never show in UI.
    const stats: PortfolioStats = botStats.status === 'fulfilled'
      ? botStats.value
      : { ...demoStats(), win_rate: 0, total_trades: 0, total_pnl: 0, today_pnl: 0, sharpe_ratio: 0 }

    if (account.status === 'fulfilled') {
      const acc = account.value
      const todayPnl = parseFloat(acc.equity) - parseFloat(acc.last_equity)
      if (!isNaN(todayPnl)) stats.today_pnl = +todayPnl.toFixed(2)

      if (history.status === 'fulfilled') {
        const base     = history.value.base_value
        const totalPnl = parseFloat(acc.equity) - base
        if (base > 0 && !isNaN(totalPnl)) stats.total_pnl = +totalPnl.toFixed(2)
      }
    }

    return NextResponse.json(stats)
  } catch {
    return NextResponse.json({ ...demoStats(), win_rate: 0, total_trades: 0, total_pnl: 0, today_pnl: 0, sharpe_ratio: 0 })
  }
}
