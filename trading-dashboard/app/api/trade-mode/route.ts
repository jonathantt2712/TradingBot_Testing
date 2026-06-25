/**
 * GET  /api/trade-mode  → current execution mode { auto_execute }
 * POST /api/trade-mode  → toggle auto-execute { auto_execute: boolean }
 *
 * Source of truth: Neon DB (survives bot restarts and works across devices).
 * On every GET we also sync the DB value to the bot so it stays in sync
 * even after a Railway restart that wiped trade_mode.json.
 */
import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'
import { botPost } from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function GET() {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const user = await prisma.user.findUnique({
    where:  { id: session.user.id },
    select: { autoExecute: true },
  })
  const autoExecute = user?.autoExecute ?? false

  // Sync to bot in the background so the file stays correct after restarts.
  // Fire-and-forget — we don't block the response on bot availability.
  botPost('/api/trade-mode', { auto_execute: autoExecute }).catch(() => {})

  return NextResponse.json({ auto_execute: autoExecute })
}

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  let body: { auto_execute?: boolean }
  try { body = await req.json() } catch {
    return NextResponse.json({ error: 'Invalid body' }, { status: 400 })
  }

  const autoExecute = !!body.auto_execute

  // Save to DB — the durable source of truth.
  await prisma.user.update({
    where: { id: session.user.id },
    data:  { autoExecute },
  })

  // Also push to bot. If bot is offline this is best-effort;
  // the next GET will re-sync once it comes back online.
  botPost('/api/trade-mode', { auto_execute: autoExecute }).catch(() => {})

  return NextResponse.json({ status: 'ok', auto_execute: autoExecute })
}
