/**
 * GET  /api/trade-mode  → current execution mode { auto_execute }
 * POST /api/trade-mode  → toggle auto-execute { auto_execute: boolean }
 *
 * The BOT's file is the source of truth for what the bot actually does — it
 * is shared state, not per-user state. GET is strictly read-only: the old
 * behaviour of syncing the requesting user's personal DB value to the bot on
 * every page load let ANY signed-in account silently overwrite the shared
 * toggle (user B loading the dashboard disarmed — or armed — the bot user A
 * configured). Only an explicit POST may change the bot.
 */
import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'
import { botGet, botPost } from '@/lib/bot-api'
import { isOwner } from '@/lib/session'

export const dynamic = 'force-dynamic'

export async function GET() {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const data = await botGet<{ auto_execute?: boolean }>('/api/trade-mode')
    return NextResponse.json({ auto_execute: !!data.auto_execute })
  } catch {
    // Bot offline — fall back to this user's stored preference for display only.
    const user = await prisma.user.findUnique({
      where:  { id: session.user.id },
      select: { autoExecute: true },
    })
    return NextResponse.json({ auto_execute: user?.autoExecute ?? false, bot_offline: true })
  }
}

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!(await isOwner())) {
    return NextResponse.json(
      { error: 'Owner role required — this changes the shared bot for everyone' },
      { status: 403 },
    )
  }


  let body: { auto_execute?: boolean }
  try { body = await req.json() } catch {
    return NextResponse.json({ error: 'Invalid body' }, { status: 400 })
  }

  const autoExecute = !!body.auto_execute

  // Remember the user's preference (display fallback when the bot is offline).
  await prisma.user.update({
    where: { id: session.user.id },
    data:  { autoExecute },
  })

  // Push the explicit change to the bot.
  botPost('/api/trade-mode', { auto_execute: autoExecute }).catch(() => {})

  return NextResponse.json({ status: 'ok', auto_execute: autoExecute })
}
