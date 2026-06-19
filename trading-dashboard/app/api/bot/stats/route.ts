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
        // (e.g. ephemeral filesystem on Railway). Fall back to daily win rate from
        // Alpaca portfolio history: count days with positive P&L change.
        if (stats.win_rate === 0) {
          let tradingDays = 0, winDays = 0
          for (let i = 1; i < pl.length; i++) {
            const daily = (pl[i] ?? 0) - (pl[i - 1] ?? 0)
            if (Math.abs(daily) > 0.01) {
              tradingDays++
              if (daily > 0) winDays++
            }
          }
          if (tradingDays > 0) stats.win_rate = +(winDays / tradingDays * 100).toFixed(1)
        }
      }

      // open_positions is set by the frontend from the live positions fetch
    }

    return NextResponse.json(stats)
  } catch {
    return NextResponse.json(demoStats())
  }
}
