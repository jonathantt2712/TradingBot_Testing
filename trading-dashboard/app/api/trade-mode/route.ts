/**
 * GET  /api/trade-mode  → current execution mode { auto_execute }
 * POST /api/trade-mode  → toggle auto-execute { auto_execute: boolean }
 *
 * Proxies the bot server's /api/trade-mode. Controls whether live_runner
 * places orders itself (auto) or only generates signals for manual approval.
 */
import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { botGet, botPost } from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botGet('/api/trade-mode')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Bot offline', auto_execute: false }, { status: 502 })
  }
}

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  let body: { auto_execute?: boolean }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid body' }, { status: 400 })
  }
  try {
    const data = await botPost('/api/trade-mode', { auto_execute: !!body.auto_execute })
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Failed to update trade mode' }, { status: 502 })
  }
}
