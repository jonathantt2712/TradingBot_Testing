import { NextResponse } from 'next/server'
import { botGet }       from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const data = await botGet<{ status: string; agents: boolean; timestamp: string }>('/api/health')
    return NextResponse.json({ ok: true, ...data })
  } catch {
    return NextResponse.json({ ok: false, status: 'offline' }, { status: 503 })
  }
}
