import { NextResponse } from 'next/server'
import { botPost } from '@/lib/bot-api'
import type { LearningData } from '@/types/trading'

export const dynamic = 'force-dynamic'

export async function POST() {
  try {
    const data = await botPost<LearningData>('/api/learning/simulate', {})
    return NextResponse.json(data)
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 })
  }
}
