import { NextResponse } from 'next/server'
import { auth } from '@/auth'

export const dynamic = 'force-dynamic'

const BOT_URL = (process.env.TRADING_BOT_API_URL ?? process.env.BOT_URL ?? 'http://localhost:8000').trim().replace(/\/+$/, '')
const BOT_SECRET = process.env.BOT_API_SECRET ?? ''
const BOT_HEADERS: Record<string, string> = {
  'ngrok-skip-browser-warning': '1',
  ...(BOT_SECRET ? { 'x-bot-secret': BOT_SECRET } : {}),
}

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const res = await fetch(`${BOT_URL}/api/backtest/log`, { headers: BOT_HEADERS, cache: 'no-store' })
    const text = await res.text()
    return new NextResponse(text, { headers: { 'Content-Type': 'text/plain; charset=utf-8' } })
  } catch {
    return new NextResponse('Bot offline or no log available.', { status: 502, headers: { 'Content-Type': 'text/plain' } })
  }
}
