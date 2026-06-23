import { NextResponse } from 'next/server'
import { botGet }       from '@/lib/bot-api'
import type { ValidationData } from '@/types/trading'

export const dynamic = 'force-dynamic'

const EMPTY: ValidationData = {
  trades: 0, verdict: 'inconclusive', message: 'bot offline',
  equity: [], drawdown: [],
}

export async function GET() {
  try {
    const data = await botGet<ValidationData>('/api/validation')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(EMPTY)
  }
}
