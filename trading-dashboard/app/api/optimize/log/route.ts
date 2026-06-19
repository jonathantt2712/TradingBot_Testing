import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { botGet } from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botGet<{ running: boolean; lines: string[]; status: string | null }>('/api/optimize/log')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Bot offline' }, { status: 502 })
  }
}
