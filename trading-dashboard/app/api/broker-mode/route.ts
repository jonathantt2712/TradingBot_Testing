/**
 * GET  /api/broker-mode  → current execution broker { broker: 'alpaca' | 'ibkr' }
 * POST /api/broker-mode  → switch broker { broker: 'alpaca' | 'ibkr' }
 *
 * Proxies the bot server's /api/broker-mode. live_runner polls the same value
 * and restarts its trading session on the newly-selected broker (flattening
 * open positions first when auto-execute is live).
 */
import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { botGet, botPost } from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botGet('/api/broker-mode')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Bot offline', broker: 'alpaca' }, { status: 502 })
  }
}

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  let body: { broker?: string }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid body' }, { status: 400 })
  }
  const broker = (body.broker || '').toLowerCase()
  if (broker !== 'alpaca' && broker !== 'ibkr') {
    return NextResponse.json({ error: "broker must be 'alpaca' or 'ibkr'" }, { status: 400 })
  }
  try {
    const data = await botPost('/api/broker-mode', { broker })
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Failed to switch broker' }, { status: 502 })
  }
}
