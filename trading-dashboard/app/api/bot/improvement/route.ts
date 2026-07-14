import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { botGet } from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

/** Self-improvement timeline: what the bot changed about itself, when, why. */
export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botGet('/api/improvement-history')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ history: [], bot_offline: true }, { status: 200 })
  }
}
