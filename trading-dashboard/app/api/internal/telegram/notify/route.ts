/**
 * POST /api/internal/telegram/notify
 * Called by the Python bot when a trade or market event occurs.
 * Reads all subscribed chat_ids from DB and sends messages via Telegram.
 */
import { NextResponse } from 'next/server'
import { prisma }       from '@/lib/prisma'

const BOT_TOKEN  = process.env.TELEGRAM_BOT_TOKEN ?? ''
const BOT_SECRET = process.env.BOT_API_SECRET     ?? ''
const TG_API     = `https://api.telegram.org/bot${BOT_TOKEN}`

async function tgSend(chatId: string, text: string) {
  await fetch(`${TG_API}/sendMessage`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ chat_id: chatId, text: text.slice(0, 4000), parse_mode: 'HTML', disable_web_page_preview: true }),
  }).catch(() => {})
}

async function broadcast(text: string) {
  const users = await prisma.user.findMany({
    where:  { telegramChatId: { not: null } },
    select: { telegramChatId: true },
  })
  await Promise.all(users.map(u => tgSend(u.telegramChatId!, text)))
}

function fmtCurrency(n: number) {
  return n >= 0 ? `+$${n.toFixed(2)}` : `-$${Math.abs(n).toFixed(2)}`
}

export async function POST(req: Request) {
  // Verify bot secret
  const secret = req.headers.get('x-bot-secret') ?? ''
  if (BOT_SECRET && secret !== BOT_SECRET) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }
  if (!BOT_TOKEN) return NextResponse.json({ ok: true })

  let body: any
  try { body = await req.json() } catch { return NextResponse.json({ error: 'Bad JSON' }, { status: 400 }) }

  const { type, data } = body

  if (type === 'trade_entry') {
    const { ticker, direction, entry = 0, stop_loss = 0, take_profit = 0,
            qty = 0, composite_score = 0, rationale = '' } = data
    const rr        = stop_loss ? Math.abs(take_profit - entry) / Math.abs(entry - stop_loss) : 0
    const totalCost = qty * entry
    const risk$     = qty * Math.abs(entry - stop_loss)
    const upside$   = qty * Math.abs(take_profit - entry)
    const arrow     = direction === 'LONG' ? '🟢' : '🔴'

    const lines = [
      `${arrow} <b>New Trade — ${direction === 'LONG' ? 'Long' : 'Short'} ${ticker}</b>`,
      '',
      `📌 <b>Entry:</b> $${entry.toFixed(2)}   |   <b>Qty:</b> ${qty}   |   <b>Total:</b> $${totalCost.toLocaleString('en', { maximumFractionDigits: 0 })}`,
      `🎯 <b>Target:</b> $${take_profit.toFixed(2)}   |   <b>Stop:</b> $${stop_loss.toFixed(2)}`,
      `⚖️ <b>R/R:</b> ${rr.toFixed(2)}x   |   <b>Risk:</b> $${risk$.toFixed(0)}   |   <b>Upside:</b> $${upside$.toFixed(0)}`,
      `💡 <b>Score:</b> ${composite_score.toFixed(0)}/100`,
    ]
    if (rationale) lines.push('', `📝 ${rationale.slice(0, 200)}`)
    await broadcast(lines.join('\n'))
  }

  else if (type === 'trade_exit') {
    const { ticker, direction, entry = 0, exit_price = 0, qty = 0, reason = '', pnl } = data
    const realPnl   = pnl ?? (direction === 'LONG' ? (exit_price - entry) * qty : (entry - exit_price) * qty)
    const pctMove   = entry ? ((exit_price - entry) / entry * 100) * (direction === 'SHORT' ? -1 : 1) : 0
    const arrow     = realPnl >= 0 ? '✅' : '❌'
    const friendly  = reason.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())

    const lines = [
      `${arrow} <b>Position Closed — ${ticker}</b>`,
      '',
      `📊 <b>${realPnl >= 0 ? 'Profit' : 'Loss'}:</b> ${fmtCurrency(realPnl)}  (${pctMove >= 0 ? '+' : ''}${pctMove.toFixed(2)}%)`,
      `📌 <b>Entry:</b> $${entry.toFixed(2)}   →   <b>Exit:</b> $${exit_price.toFixed(2)}`,
      `📦 <b>Qty:</b> ${qty} shares`,
    ]
    if (friendly) lines.push('', `📋 <b>Reason:</b> ${friendly}`)
    await broadcast(lines.join('\n'))
  }

  else if (type === 'market_event') {
    const { headline = '', detail = '' } = data
    await broadcast(`📡 <b>Market Update</b>\n\n${headline}${detail ? '\n\n' + detail.slice(0, 300) : ''}`)
  }

  else if (type === 'weekly_summary') {
    const { total_trades = 0, wins = 0, losses = 0, total_pnl = 0, best_trade, worst_trade } = data
    const winRate   = total_trades ? (wins / total_trades * 100).toFixed(1) : '0.0'
    const pnlEmoji  = total_pnl >= 0 ? '📈' : '📉'
    const now       = new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })

    const lines = [
      '📊 <b>Weekly Summary</b>',
      `Week of ${now}`,
      '',
      `${pnlEmoji} <b>Total P&amp;L:</b> ${fmtCurrency(total_pnl)}`,
      `🏆 <b>Win Rate:</b> ${winRate}%  (${wins}W / ${losses}L / ${total_trades} trades)`,
    ]
    if (best_trade?.ticker)  lines.push(`⭐ <b>Best Trade:</b> ${best_trade.ticker} +$${Math.abs(best_trade.pnl ?? 0).toFixed(0)}`)
    if (worst_trade?.ticker) lines.push(`💔 <b>Worst Trade:</b> ${worst_trade.ticker} -$${Math.abs(worst_trade.pnl ?? 0).toFixed(0)}`)
    lines.push('', 'Keep it up — see you next week! 🚀')

    // Weekly summary: send to users active for 7+ days
    const cutoff = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)
    const users  = await prisma.user.findMany({
      where:  { telegramChatId: { not: null }, telegramActivatedAt: { lte: cutoff } },
      select: { telegramChatId: true },
    })
    await Promise.all(users.map(u => tgSend(u.telegramChatId!, lines.join('\n'))))
  }

  return NextResponse.json({ ok: true })
}
