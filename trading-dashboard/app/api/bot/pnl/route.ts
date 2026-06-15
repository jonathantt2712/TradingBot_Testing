import { NextResponse } from 'next/server'
import { getAlpacaCreds } from '@/lib/session'
import { getPortfolioHistory } from '@/lib/alpaca'
import { botGet } from '@/lib/bot-api'
import { demoPnL } from '@/lib/api'
import type { PnLPoint } from '@/types/trading'

export async function GET() {
  // Try Alpaca portfolio history first — gives real equity curve data
  try {
    const creds = await getAlpacaCreds()
    if (creds) {
      const hist = await getPortfolioHistory(creds)
      if (hist.timestamp?.length) {
        const points: PnLPoint[] = hist.timestamp.map((ts, i) => ({
          date:           new Date(ts * 1000).toISOString().slice(0, 10),
          cumulative_pnl: +((hist.profit_loss?.[i] ?? 0)).toFixed(2),
          daily_pnl:      +(
            i > 0
              ? (hist.profit_loss?.[i] ?? 0) - (hist.profit_loss?.[i - 1] ?? 0)
              : (hist.profit_loss?.[0] ?? 0)
          ).toFixed(2),
          trade_count: 0,
        }))
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
