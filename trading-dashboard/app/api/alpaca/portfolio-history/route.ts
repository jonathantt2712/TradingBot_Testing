import { NextResponse } from 'next/server'
import { getPortfolioHistory } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import type { PnLPoint } from '@/types/trading'

export async function GET(req: Request) {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const { searchParams } = new URL(req.url)
  const period    = searchParams.get('period')    ?? '1M'
  const timeframe = searchParams.get('timeframe') ?? '1D'
  const intraday  = timeframe !== '1D'

  try {
    const history = await getPortfolioHistory(creds, period, timeframe)
    const { timestamp, profit_loss } = history

    const points: PnLPoint[] = timestamp.map((ts, i) => {
      const cum   = profit_loss[i] ?? 0
      const prev  = i > 0 ? (profit_loss[i - 1] ?? 0) : 0
      const daily = i === 0 ? cum : cum - prev

      // For intraday: send raw Unix timestamp (ms) as string — client formats in local timezone
      const date = intraday
        ? String(ts * 1000)
        : new Date(ts * 1000).toISOString().slice(0, 10)

      return { date, cumulative_pnl: +cum.toFixed(2), daily_pnl: +daily.toFixed(2), trade_count: 0 }
    })

    return NextResponse.json(points)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
