import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { getAccount, getPortfolioHistory, getFills, winRateFromFills } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoStats } from '@/lib/api'
import type { PortfolioStats } from '@/types/trading'

export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const [botStats, account, history, fills] = await Promise.allSettled([
      botGet<PortfolioStats>('/api/stats'),
      getAccount(creds),
      getPortfolioHistory(creds, '1A', '1D'),
      getFills(creds),
    ])

    const stats: PortfolioStats = botStats.status === 'fulfilled'
      ? botStats.value
      : demoStats()

    if (account.status === 'fulfilled') {
      const acc = account.value
      const todayPnl = parseFloat(acc.equity) - parseFloat(acc.last_equity)
      if (!isNaN(todayPnl)) stats.today_pnl = +todayPnl.toFixed(2)

      if (history.status === 'fulfilled') {
        const base     = history.value.base_value
        const totalPnl = parseFloat(acc.equity) - base
        if (base > 0 && !isNaN(totalPnl)) stats.total_pnl = +totalPnl.toFixed(2)
      }

      // Always prefer real Alpaca fill-based win rate over bot's local history
      // (bot history may be empty on ephemeral filesystems like Railway).
      if (fills.status === 'fulfilled' && Array.isArray(fills.value)) {
        const computed = winRateFromFills(fills.value)
        if (computed !== null) stats.win_rate = computed
      }

      // open_positions is set by the frontend from the live positions fetch
    }

    return NextResponse.json(stats)
  } catch {
    return NextResponse.json(demoStats())
  }
}
