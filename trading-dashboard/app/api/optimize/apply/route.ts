import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { botPost } from '@/lib/bot-api'
import { isOwner } from '@/lib/session'

export const dynamic = 'force-dynamic'

export async function POST() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!(await isOwner())) {
    return NextResponse.json(
      { error: 'Owner role required — this changes the shared bot for everyone' },
      { status: 403 },
    )
  }

  try {
    const data = await botPost('/api/optimize/apply', {})
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ status: 'error', reason: 'Failed to reach bot' }, { status: 502 })
  }
}
