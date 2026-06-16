import { NextResponse } from 'next/server'
import { auth }         from '@/auth'
import { botGet }       from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const data = await botGet<{ results: unknown; optimal: unknown; configText: string | null }>('/api/backtest')
    return NextResponse.json(data, { headers: { 'Cache-Control': 'no-store' } })
  } catch {
    return NextResponse.json(
      { results: null, optimal: null, configText: null },
      { status: 502 },
    )
  }
}
