import { NextResponse }       from 'next/server'
import { getAlpacaCreds }    from '@/lib/session'
import { getPortfolioHistory } from '@/lib/alpaca'
import { botGet }            from '@/lib/bot-api'
import { demoPnL }           from '@/lib/api'
import type { PnLPoint }     from '@/types/trading'

export async function GET() {
  // Try Alpaca portfolio history first — gives real equity curve data
  try {
    const creds = await getAlpacaCreds()
    if (creds) {
      const hist = await getPortfolioHistory(creds)
      if (hist.timestamp?.length) {
        const base    = hist.base_value || (hist.equity ?? []).find((e: number) => e > 0) || 0
        const profits = hist.profit_loss ?? []
        const equities = hist.equity ?? []

        const points: PnLPoint[] = hist.timestamp
          .map((ts: number, i: number) => ({
            date:           new Date(ts * 1000).toISOString().slice(0, 10),
            // cumulative P&L = equity relative to the base (start of period)
            cumulative_pnl: +((equities[i] ?? 0) - base).toFixed(2),
            // daily P&L = what Alpaca reports for that day
            daily_pnl:      +(profits[i] ?? 0).toFixed(2),
            trade_count:    0,
            equity:         +(equities[i] ?? 0).toFixed(2),
          }))
          .filter((p: PnLPoint) => (p.equity ?? 0) > 0)

        return NextResponse.json(points)
      }
    }
  } catch (e) {
    console.warn('[pnl] Alpaca portfolio history failed, falling back to bot:', e)
  }

  // Fall back to bot's trades.json-based pnl
  try {
    const data = await botGet('/api/pnl')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(demoPnL())
  }
}
