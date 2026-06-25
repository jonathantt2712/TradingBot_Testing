import { NextResponse } from 'next/server'
import { botGet }       from '@/lib/bot-api'
import { auth }         from '@/auth'
import type { LearningData } from '@/types/trading'

export const dynamic = 'force-dynamic'

const EMPTY: LearningData = {
  active: false, history: [], weights: {}, multipliers: {},
  win_rate: null, long_win_rate: null, short_win_rate: null, bias: 'neutral',
  long_threshold: null, short_threshold: null, sample_size: 0, steps: 0,
}

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botGet<LearningData>('/api/learning')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(EMPTY)
  }
}
