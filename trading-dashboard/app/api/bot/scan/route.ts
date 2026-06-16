import { NextResponse } from 'next/server'
import { botPost }      from '@/lib/bot-api'
import { auth }         from '@/auth'

export const dynamic = 'force-dynamic'

export async function POST() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botPost('/api/scan', {})
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ ok: false, error: 'Bot server offline' }, { status: 503 })
  }
}
