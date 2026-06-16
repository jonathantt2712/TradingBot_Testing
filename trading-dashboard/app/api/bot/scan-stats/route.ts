import { NextResponse } from 'next/server'
import { botGet }       from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

import { auth } from '@/auth'

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
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
