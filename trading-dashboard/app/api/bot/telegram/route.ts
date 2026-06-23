import { NextResponse }   from 'next/server'
import { auth }           from '@/auth'
import { botGet, botPost } from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

/** GET /api/bot/telegram — return link status for the current user */
export async function GET() {
  const session = await auth()
  if (!session?.user?.email) return NextResponse.json({ linked: false }, { status: 401 })
  try {
    const status = await botGet<{ linked: boolean; activated_at?: string }>(
      `/api/telegram/status?email=${encodeURIComponent(session.user.email)}`
    )
    return NextResponse.json(status)
  } catch {
    return NextResponse.json({ linked: false })
  }
}

/** POST /api/bot/telegram  body: { action: 'register' | 'unlink' } */
export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user?.email) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const { action } = await req.json()
  const email = session.user.email

  if (action === 'register') {
    try {
      const data = await botPost<{ token: string; bot_username: string }>(
        '/api/telegram/register',
        { email }
      )
      return NextResponse.json(data)
    } catch {
      return NextResponse.json({ error: 'Bot unavailable' }, { status: 502 })
    }
  }

  if (action === 'unlink') {
    try {
      await botPost('/api/telegram/unlink', { email })
      return NextResponse.json({ ok: true })
    } catch {
      return NextResponse.json({ error: 'Bot unavailable' }, { status: 502 })
    }
  }

  return NextResponse.json({ error: 'Unknown action' }, { status: 400 })
}
