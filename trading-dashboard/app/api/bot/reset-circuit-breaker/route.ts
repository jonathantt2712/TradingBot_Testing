import { NextResponse } from 'next/server'
import { botPost }      from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

import { auth } from '@/auth'
import { isOwner } from '@/lib/session'

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
    const data = await botPost('/api/reset-circuit-breaker', {})
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ ok: false }, { status: 503 })
  }
}
