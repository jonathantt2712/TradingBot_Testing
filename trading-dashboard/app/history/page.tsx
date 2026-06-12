export const dynamic = 'force-dynamic'
import { HistoryTable } from '@/components/history/HistoryTable'
import { getOrders, type AlpacaCreds } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { botGet }       from '@/lib/bot-api'
import { demoHistory }  from '@/lib/api'
import type { TradeRecord } from '@/types/trading'

async function loadHistory(creds: AlpacaCreds | null): Promise<{ trades: TradeRecord[]; live: boolean }> {
  const [botHistory, alpacaOrders] = await Promise.allSettled([
    botGet<TradeRecord[]>('/api/history'),
    creds ? getOrders(creds, 'closed', 100) : Promise.reject(new Error('no creds')),
  ])

  const botTrades = botHistory.status === 'fulfilled' ? botHistory.value : []

  // Convert Alpaca filled orders → TradeRecord shape
  const fromAlpaca: TradeRecord[] = alpacaOrders.status === 'fulfilled'
    ? alpacaOrders.value
        .filter(o => parseFloat(o.filled_qty ?? '0') > 0)
        .map(o => ({
          id:        o.id,
          ticker:    o.symbol,
          direction: o.side === 'buy' ? 'LONG' : 'SHORT',
          entry:     parseFloat(o.filled_avg_price ?? '0'),
          exit:      null,
          qty:       parseInt(o.filled_qty),
          pnl:       null,
          pnl_pct:   null,
          opened_at: o.created_at,
          closed_at: o.filled_at,
          duration:  null,
          status:    'closed' as const,
          order_id:  o.id,
        }))
    : []

  // Bot records take precedence (they have P&L calculation)
  const botIds = new Set(botTrades.map(t => t.order_id).filter(Boolean))
  const merged = [
    ...botTrades,
    ...fromAlpaca.filter(t => !botIds.has(t.id)),
  ].sort((a, b) => b.opened_at.localeCompare(a.opened_at))

  const live = botHistory.status === 'fulfilled' || alpacaOrders.status === 'fulfilled'
  return { trades: merged.length ? merged : demoHistory(), live }
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
