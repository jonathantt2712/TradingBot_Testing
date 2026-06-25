import { NextResponse } from 'next/server'
import { botGet }       from '@/lib/bot-api'
import { auth }         from '@/auth'

export const dynamic = 'force-dynamic'

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ ok: false, status: 'unauthorized' }, { status: 401 })
  try {
    const data = await botGet<{ status: string; agents: boolean; timestamp: string }>('/api/health')
    return NextResponse.json({ ok: true, ...data })
  } catch (err) {
    console.error('bot health check failed:', err instanceof Error ? err.message : err)
    return NextResponse.json({ ok: false, status: 'offline' }, { status: 503 })
  }
}
