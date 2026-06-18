import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { botPost } from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  const mode = new URL(req.url).searchParams.get('mode') || 'run'
  try {
    const data = await botPost(`/api/challenge/run?mode=${encodeURIComponent(mode)}`, {})
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ status: 'error', reason: 'Failed to reach bot' }, { status: 502 })
  }
}
