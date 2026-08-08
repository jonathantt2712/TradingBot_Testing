/**
 * GET  /api/trade-mode  → { auto_execute, bot: { auto_execute, armed, disarmed_reason } | null }
 * POST /api/trade-mode  → save preference { auto_execute: boolean }
 *
 * Two mechanisms sit behind one switch:
 *   • per-user (everyone) — the dashboard executes on this user's behalf while
 *     the Trades page is open, using their own Alpaca account;
 *   • the bot's own executor (OWNERS only) — the switch is forwarded to the bot
 *     server so it keeps entering trades with no browser open. Without this the
 *     toggle only ever set a DB flag and the bot never left manual mode.
 *
 * `bot` reports what the bot server says about itself so an auto mode the
 * deploy can't act on (AUTO_EXECUTE_ON_RAILWAY off, live account, missing keys)
 * is visible instead of silent.
 */
import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'
import { isOwner } from '@/lib/session'
import { botGet, botPost } from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

interface BotTradeMode {
  auto_execute: boolean
  armed: boolean
  disarmed_reason: string | null
}

export async function GET() {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const user = await prisma.user.findUnique({
    where:  { id: session.user.id },
    select: { autoExecute: true },
  })

  let bot: BotTradeMode | null = null
  try {
    bot = await botGet<BotTradeMode>('/api/trade-mode')
  } catch {
    bot = null   // bot offline — the per-user preference still stands
  }

  return NextResponse.json({ auto_execute: user?.autoExecute ?? false, bot })
}

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  let body: { auto_execute?: boolean }
  try { body = await req.json() } catch {
    return NextResponse.json({ error: 'Invalid body' }, { status: 400 })
  }

  const autoExecute = !!body.auto_execute
  await prisma.user.update({
    where: { id: session.user.id },
    data:  { autoExecute },
  })

  // Owners also drive the shared bot: this is what makes auto mode trade
  // without the dashboard open. Viewers change only their own preference.
  let bot: BotTradeMode | null = null
  if (await isOwner()) {
    try {
      bot = await botPost<BotTradeMode>('/api/trade-mode', { auto_execute: autoExecute })
    } catch {
      return NextResponse.json(
        { status: 'partial', auto_execute: autoExecute,
          error: 'Saved your preference, but the bot server is unreachable — it is still in its previous mode' },
        { status: 503 },
      )
    }
  }

  return NextResponse.json({ status: 'ok', auto_execute: autoExecute, bot })
}
