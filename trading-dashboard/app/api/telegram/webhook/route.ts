/**
 * POST /api/telegram/webhook  — receives updates from Telegram
 * GET  /api/telegram/webhook  — registers the webhook with Telegram (call once after deploy)
 */
import { NextResponse } from 'next/server'
import { prisma }       from '@/lib/prisma'

const BOT_TOKEN  = process.env.TELEGRAM_BOT_TOKEN ?? ''
const TG_API     = `https://api.telegram.org/bot${BOT_TOKEN}`
const TOKEN_TTL  = 10 * 60 * 1000  // 10 minutes

async function tgSend(chatId: string, text: string) {
  if (!BOT_TOKEN) return
  await fetch(`${TG_API}/sendMessage`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ chat_id: chatId, text, parse_mode: 'HTML', disable_web_page_preview: true }),
  }).catch(() => {})
}

/** Register the webhook with Telegram — call once after deploy */
export async function GET() {
  if (!BOT_TOKEN) return NextResponse.json({ error: 'TELEGRAM_BOT_TOKEN not set' }, { status: 503 })
  const url  = process.env.NEXTJS_URL
  if (!url)  return NextResponse.json({ error: 'NEXTJS_URL not set' }, { status: 503 })

  const res  = await fetch(`${TG_API}/setWebhook?url=${url}/api/telegram/webhook`)
  const data = await res.json()
  return NextResponse.json(data)
}

/** Handle incoming Telegram update */
export async function POST(req: Request) {
  if (!BOT_TOKEN) return NextResponse.json({ ok: true })

  let update: any
  try { update = await req.json() } catch { return NextResponse.json({ ok: true }) }

  const msg     = update?.message
  const text    = (msg?.text ?? '').trim()
  const chatId  = String(msg?.chat?.id ?? '')
  if (!chatId || !text.startsWith('/start')) return NextResponse.json({ ok: true })

  const parts = text.split(/\s+/)
  const token = parts[1] ?? ''

  if (!token) {
    await tgSend(chatId, '👋 <b>TradingBot</b>\n\nUse the profile page to connect your account and enable alerts.')
    return NextResponse.json({ ok: true })
  }

  // Validate token
  const user = await prisma.user.findFirst({
    where: { telegramToken: token },
    select: { id: true, email: true, telegramTokenAt: true, telegramChatId: true },
  })

  if (!user || !user.telegramTokenAt) {
    await tgSend(chatId, '⚠️ <b>Invalid or expired link.</b>\n\nGo back to your profile page and click <b>Connect Telegram</b> again.')
    return NextResponse.json({ ok: true })
  }

  if (Date.now() - user.telegramTokenAt.getTime() > TOKEN_TTL) {
    await prisma.user.update({ where: { id: user.id }, data: { telegramToken: null, telegramTokenAt: null } })
    await tgSend(chatId, '⏰ <b>Link expired.</b>\n\nGo back to your profile page and connect again — the link is valid for 10 minutes.')
    return NextResponse.json({ ok: true })
  }

  // Save chat_id, clear token
  await prisma.user.update({
    where: { id: user.id },
    data:  { telegramChatId: chatId, telegramToken: null, telegramTokenAt: null, telegramActivatedAt: new Date() },
  })

  await tgSend(chatId, [
    '👋 <b>Welcome to TradingBot Alerts!</b>',
    '',
    "You're all set. Here's what I'll send you:",
    '',
    '📥 <b>Trade Entry</b> — ticker, direction, entry price, target, stop, R/R, and the reasoning.',
    '📤 <b>Trade Exit</b> — exit price, profit/loss, and why we closed.',
    '📡 <b>Market Events</b> — notable regime shifts or unusual moves.',
    '📊 <b>Weekly Summary</b> — every Monday: trades, P&amp;L, win rate.',
    '',
    "Stay sharp, and let's make some money 🚀",
  ].join('\n'))

  return NextResponse.json({ ok: true })
}
