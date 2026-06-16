import { NextResponse } from 'next/server'
import { getPortfolioHistory } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import type { PnLPoint } from '@/types/trading'

export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const history = await getPortfolioHistory(creds, '1M', '1D')
    const { timestamp, profit_loss } = history

    const points: PnLPoint[] = timestamp.map((ts, i) => {
      const cum   = profit_loss[i] ?? 0
      const prev  = i > 0 ? (profit_loss[i - 1] ?? 0) : 0
      const daily = i === 0 ? cum : cum - prev
      const date  = new Date(ts * 1000).toISOString().slice(0, 10)
      return { date, cumulative_pnl: +cum.toFixed(2), daily_pnl: +daily.toFixed(2), trade_count: 0 }
    })

    return NextResponse.json(points)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
