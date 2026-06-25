import { NextResponse } from 'next/server'
import { botPost } from '@/lib/bot-api'
import { auth }    from '@/auth'
import type { LearningData } from '@/types/trading'

export const dynamic = 'force-dynamic'

export async function POST() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botPost<LearningData>('/api/learning/simulate', {})
    return NextResponse.json(data)
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 })
  }
}
