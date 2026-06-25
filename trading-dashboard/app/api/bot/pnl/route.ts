import { NextResponse }        from 'next/server'
import { getAlpacaCreds }      from '@/lib/session'
import { getPortfolioHistory } from '@/lib/alpaca'
import { botGet }              from '@/lib/bot-api'
import { demoPnL }             from '@/lib/api'
import type { PnLPoint }       from '@/types/trading'

export async function GET() {
  // Try Alpaca portfolio history first — gives real equity curve data
  try {
    const creds = await getAlpacaCreds()
    if (creds) {
      const hist     = await getPortfolioHistory(creds)
      const ts       = hist.timestamp   ?? []
      const equities = hist.equity      ?? []
      const profits  = hist.profit_loss ?? []

      if (ts.length > 0) {
        const base = hist.base_value || equities.find((e: number) => e > 0) || 0

        const points: PnLPoint[] = ts.map((t: number, i: number) => {
          const eq  = equities[i] ?? 0
          const pl  = profits[i]  ?? 0
          return {
            date:           new Date(t * 1000).toISOString().slice(0, 10),
            cumulative_pnl: base > 0 ? +(eq - base).toFixed(2) : +pl.toFixed(2),
            daily_pnl:      +pl.toFixed(2),
            trade_count:    0,
            equity:         +eq.toFixed(2),
          }
        // Only filter out zero-equity if we actually have equity data —
        // avoids emptying the array when Alpaca doesn't return equity values.
        }).filter((p: PnLPoint) => equities.some((e: number) => e > 0) ? (p.equity ?? 0) > 0 : true)

        if (points.length > 0) return NextResponse.json(points)
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
