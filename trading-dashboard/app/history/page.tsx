export const dynamic = 'force-dynamic'
import { HistoryTable } from '@/components/history/HistoryTable'
import { getOrders, tradesFromOrders, type AlpacaCreds } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { botGet }       from '@/lib/bot-api'
import type { TradeRecord } from '@/types/trading'

async function loadHistory(creds: AlpacaCreds | null): Promise<{ trades: TradeRecord[]; live: boolean }> {
  const [botResult, ordersResult] = await Promise.allSettled([
    botGet<TradeRecord[]>('/api/history'),
    creds ? getOrders(creds, 'closed', 200) : Promise.reject(new Error('no creds')),
  ])

  const botTrades: TradeRecord[] = botResult.status === 'fulfilled'
    ? botResult.value.map(t => ({ ...t, opened_at: t.opened_at ?? (t as any).executed_at ?? '' }))
    : []

  // FIFO-match closed orders → round-trip trades with real P&L
  const orderTrades: TradeRecord[] = ordersResult.status === 'fulfilled'
    ? tradesFromOrders(ordersResult.value)
    : []

  // Bot trades take precedence (more detail); fill in the rest from Alpaca orders
  const botKeys = new Set(botTrades.map(t => `${t.ticker}-${(t.opened_at ?? '').slice(0, 10)}`))
  const merged = [
    ...botTrades,
    ...orderTrades.filter(t => !botKeys.has(`${t.ticker}-${(t.opened_at ?? '').slice(0, 10)}`)),
  ].sort((a, b) => (b.opened_at ?? '').localeCompare(a.opened_at ?? ''))

  const live = ordersResult.status === 'fulfilled' || botResult.status === 'fulfilled'
  return { trades: merged, live }
}

export default async function HistoryPage() {
  const creds = await getAlpacaCreds()
  const { trades, live } = await loadHistory(creds)

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 md:space-y-6 max-w-[1400px]">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-primary">Trade History</h1>
          <p className="text-xs text-muted mt-0.5">
            {trades.length} trades · sourced from Alpaca + bot records
          </p>
        </div>
        {live
          ? <span className="flex items-center gap-1.5 text-xs text-bull"><span className="h-1.5 w-1.5 rounded-full bg-bull animate-pulse-slow" />Live</span>
          : <span className="text-xs text-caution">Demo data — start bot API for live data</span>
        }
      </div>

      <HistoryTable trades={trades} />
    </div>
  )
}
