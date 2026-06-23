/**
 * GET  /api/bot/telegram  — return telegram link status for the current user
 * POST /api/bot/telegram  — generate link token (action: 'register') or unlink
 */
import { NextResponse } from 'next/server'
import { auth }         from '@/auth'
import { prisma }       from '@/lib/prisma'
import { randomUUID }   from 'crypto'

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN ?? ''

export const dynamic = 'force-dynamic'

async function getBotUsername(): Promise<string | null> {
  if (!BOT_TOKEN) return null
  try {
    const res  = await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/getMe`)
    const data = await res.json()
    return data?.result?.username ?? null
  } catch {
    return null
  }
}

export async function GET() {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ linked: false }, { status: 401 })

  const user = await prisma.user.findUnique({
    where:  { id: session.user.id },
    select: { telegramChatId: true, telegramActivatedAt: true },
  })

  if (user?.telegramChatId) {
    return NextResponse.json({
      linked:       true,
      activated_at: user.telegramActivatedAt?.toISOString() ?? null,
    })
  }
  return NextResponse.json({ linked: false })
}

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const { action } = await req.json()

  if (action === 'register') {
    if (!BOT_TOKEN) {
      return NextResponse.json({ error: 'Telegram not configured on server' }, { status: 503 })
    }
    const token      = randomUUID()
    const botUsername = await getBotUsername()
    if (!botUsername) {
      return NextResponse.json({ error: 'Could not reach Telegram — check TELEGRAM_BOT_TOKEN' }, { status: 502 })
    }

    await prisma.user.update({
      where: { id: session.user.id },
      data:  { telegramToken: token, telegramTokenAt: new Date() },
    })

    return NextResponse.json({ token, bot_username: botUsername })
  }

  if (action === 'unlink') {
    await prisma.user.update({
      where: { id: session.user.id },
      data:  { telegramChatId: null, telegramToken: null, telegramTokenAt: null, telegramActivatedAt: null },
    })
    return NextResponse.json({ ok: true })
  }

  return NextResponse.json({ error: 'Unknown action' }, { status: 400 })
}
