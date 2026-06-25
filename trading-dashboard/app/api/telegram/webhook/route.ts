/**
 * POST /api/telegram/webhook  — receives updates from Telegram
 * GET  /api/telegram/webhook  — registers the webhook with Telegram (call once after deploy)
 */
import { NextResponse } from 'next/server'
import { prisma }       from '@/lib/prisma'
import { auth }         from '@/auth'

const BOT_TOKEN      = process.env.TELEGRAM_BOT_TOKEN      ?? ''
const WEBHOOK_SECRET = process.env.TELEGRAM_WEBHOOK_SECRET ?? ''
const TG_API         = `https://api.telegram.org/bot${BOT_TOKEN}`
const TOKEN_TTL      = 10 * 60 * 1000  // 10 minutes

async function tgSend(chatId: string, text: string) {
  if (!BOT_TOKEN) { console.error('[telegram] BOT_TOKEN missing'); return }
  try {
    const res  = await fetch(`${TG_API}/sendMessage`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        chat_id:    chatId,
        text:       text.slice(0, 4000),
        parse_mode: 'HTML',
        disable_web_page_preview: true,
      }),
    })
    if (!res.ok) {
      const body = await res.text()
      console.error('[telegram] sendMessage failed %d: %s', res.status, body)
    }
  } catch (err) {
    console.error('[telegram] sendMessage error:', err)
  }
}

const INTRO = [
  "👋 <b>Hey! I'm TradingBot.</b>",
  '',
  "I'm an AI-powered trading assistant that monitors the US stock market and executes trades automatically — based on real-time signals from multiple analysis agents: technical, fundamental, sentiment, risk, and more.",
  '',
  "Here's what I'll send you once you connect your account:",
  '',
  '📥 <b>Trade Entry</b> — every time I open a position: ticker, direction, entry price, target, stop loss, R/R ratio, and my reasoning.',
  '📤 <b>Trade Exit</b> — when I close: exit price, profit/loss, and why I got out.',
  '📡 <b>Market Events</b> — notable regime shifts or anything worth a heads-up.',
  "📊 <b>Weekly Summary</b> — every Monday: the week's trades, P&L, and win rate.",
  '',
  '🔗 <b>To connect your account:</b> go to your <b>Profile page</b> on the dashboard and click <b>Connect Telegram</b>.',
].join('\n')

/** Register the webhook with Telegram — call once after deploy (admin only) */
export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!BOT_TOKEN) return NextResponse.json({ error: 'TELEGRAM_BOT_TOKEN not set' }, { status: 503 })
  const url = process.env.NEXTJS_URL
  if (!url)  return NextResponse.json({ error: 'NEXTJS_URL not set' }, { status: 503 })

  const webhookBody: Record<string, string> = { url: `${url}/api/telegram/webhook` }
  if (WEBHOOK_SECRET) webhookBody.secret_token = WEBHOOK_SECRET
  const res  = await fetch(`${TG_API}/setWebhook`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(webhookBody),
  })
  const setData = await res.json()

  // Diagnostics — show current webhook state and bot info
  const [infoRes, meRes] = await Promise.all([
    fetch(`${TG_API}/getWebhookInfo`),
    fetch(`${TG_API}/getMe`),
  ])
  const infoData = await infoRes.json()
  const meData   = await meRes.json()

  return NextResponse.json({
    registered:   setData,
    webhook_info: infoData?.result,
    bot:          meData?.result ? { username: meData.result.username, id: meData.result.id } : null,
    target_url:   `${url}/api/telegram/webhook`,
  })
}

/** Handle incoming Telegram update */
export async function POST(req: Request) {
  // Always return 200 so Telegram doesn't keep retrying
  try {
    if (!BOT_TOKEN) return NextResponse.json({ ok: true })

    // Verify Telegram's webhook secret if one is configured.
    // Set TELEGRAM_WEBHOOK_SECRET when registering the webhook via setWebhook.
    if (WEBHOOK_SECRET) {
      const header = (req as any).headers?.get?.('x-telegram-bot-api-secret-token') ?? ''
      if (header !== WEBHOOK_SECRET) return NextResponse.json({ ok: true })
    }

    let update: any
    try { update = await req.json() } catch { return NextResponse.json({ ok: true }) }

    const msg    = update?.message
    const text   = (msg?.text ?? '').trim()
    const chatId = String(msg?.chat?.id ?? '')
    if (!chatId || !text.startsWith('/start')) return NextResponse.json({ ok: true })

    const token = text.split(/\s+/)[1] ?? ''

    // Plain /start (or /start@botname) — send intro
    if (!token) {
      await tgSend(chatId, INTRO)
      return NextResponse.json({ ok: true })
    }

    // /start <token> — link the account
    let user: { id: string; telegramTokenAt: Date | null } | null = null
    try {
      user = await prisma.user.findFirst({
        where:  { telegramToken: token },
        select: { id: true, telegramTokenAt: true },
      })
    } catch (err) {
      console.error('[telegram] DB lookup failed:', err)
      await tgSend(chatId, '⚠️ <b>Something went wrong on our end.</b>\n\nPlease try connecting again from the profile page.')
      return NextResponse.json({ ok: true })
    }

    if (!user || !user.telegramTokenAt) {
      await tgSend(chatId, '⚠️ <b>Invalid or expired link.</b>\n\nGo back to your profile page and click <b>Connect Telegram</b> again.')
      return NextResponse.json({ ok: true })
    }

    if (Date.now() - user.telegramTokenAt.getTime() > TOKEN_TTL) {
      await prisma.user.update({ where: { id: user.id }, data: { telegramToken: null, telegramTokenAt: null } }).catch(() => {})
      await tgSend(chatId, '⏰ <b>Link expired.</b>\n\nGo back to your profile page and connect again — the link is valid for 10 minutes.')
      return NextResponse.json({ ok: true })
    }

    // Save chat_id, clear token
    try {
      await prisma.user.update({
        where: { id: user.id },
        data:  { telegramChatId: chatId, telegramToken: null, telegramTokenAt: null, telegramActivatedAt: new Date() },
      })
    } catch (err) {
      console.error('[telegram] DB update failed:', err)
      await tgSend(chatId, '⚠️ <b>Could not save your connection.</b>\n\nPlease try again from the profile page.')
      return NextResponse.json({ ok: true })
    }

    await tgSend(chatId, [
      "👋 <b>You're connected!</b>",
      '',
      "From now on I'll keep you updated on every trade — entry, exit, market events, and a weekly summary every Monday.",
      '',
      "Here's a quick overview of what you'll receive:",
      '',
      '📥 <b>Trade Entry</b> — ticker, direction, price, target, stop, R/R, and reasoning.',
      '📤 <b>Trade Exit</b> — exit price, profit/loss, and why I closed.',
      '📡 <b>Market Events</b> — notable shifts or anything worth knowing.',
      "📊 <b>Weekly Summary</b> — every Monday: the week's full performance recap.",
      '',
      "Let's make some money 🚀",
    ].join('\n'))

  } catch (err) {
    console.error('[telegram] webhook handler error:', err)
  }

  return NextResponse.json({ ok: true })
}
