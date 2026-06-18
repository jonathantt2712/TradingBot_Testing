import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { botPost } from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function POST() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botPost('/api/optimize/run', {})
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Failed to trigger optimizer' }, { status: 502 })
  }
}
