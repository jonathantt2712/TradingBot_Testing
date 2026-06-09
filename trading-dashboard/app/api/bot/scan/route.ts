import { NextResponse } from 'next/server'
import { botPost }      from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function POST() {
  try {
    const data = await botPost('/api/scan', {})
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ ok: false, error: 'Bot server offline' }, { status: 503 })
  }
}
