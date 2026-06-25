import { NextResponse } from 'next/server'
import { botGet }       from '@/lib/bot-api'
import { auth }         from '@/auth'
import type { ValidationData } from '@/types/trading'

export const dynamic = 'force-dynamic'

const EMPTY: ValidationData = {
  trades: 0, verdict: 'inconclusive', message: 'bot offline',
  equity: [], drawdown: [],
}

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botGet<ValidationData>('/api/validation')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(EMPTY)
  }
}
