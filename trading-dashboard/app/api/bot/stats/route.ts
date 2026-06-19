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
        const pl = history.value.profit_loss

        // Total P&L = current equity minus base value at start of history period.
        const base = history.value.base_value
        const totalPnl = parseFloat(acc.equity) - base
        if (base > 0 && !isNaN(totalPnl)) stats.total_pnl = +totalPnl.toFixed(2)

        // Win rate from bot history is 0 when the bot has no closed trade records
        // (e.g. ephemeral filesystem on Railway). Fall back to per-trade win rate
        // computed from Alpaca fill activities via FIFO matching.
        if (stats.win_rate === 0 && fills.status === 'fulfilled') {
          const computed = winRateFromFills(fills.value)
          if (computed !== null) stats.win_rate = computed
        }
      }

      // open_positions is set by the frontend from the live positions fetch
    }

    return NextResponse.json(stats)
  } catch {
    return NextResponse.json(demoStats())
  }
}
