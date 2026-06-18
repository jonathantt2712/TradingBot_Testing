import { NextResponse } from 'next/server'
import { auth }         from '@/auth'
import { botGet }       from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botGet<{ results: unknown; status: unknown }>('/api/challenges')
    return NextResponse.json(data, { headers: { 'Cache-Control': 'no-store' } })
  } catch {
    return NextResponse.json({ results: null, status: null }, { status: 502 })
  }
}
