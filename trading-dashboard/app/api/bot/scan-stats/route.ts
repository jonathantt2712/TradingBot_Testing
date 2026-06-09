import { NextResponse } from 'next/server'
import { botGet }       from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const data = await botGet('/api/scan-stats')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(
      { ok: false, market_open: null, scans_today: 0, tickers_scanned: 0, scan_errors: 0 },
      { status: 503 },
    )
  }
}
