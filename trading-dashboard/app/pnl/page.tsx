export const dynamic = 'force-dynamic'
import { PnLAnalytics } from '@/components/pnl/PnLAnalytics'
import { botGet }       from '@/lib/bot-api'
import { getAccount, getOrders, type AlpacaCreds } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoPnL, demoStats, demoHistory } from '@/lib/api'
import type { PnLPoint, PortfolioStats, TradeRecord } from '@/types/trading'

async function loadPnL(creds: AlpacaCreds | null) {
  const [pnl, stats, account, orders] = await Promise.allSettled([
    botGet<PnLPoint[]>('/api/pnl'),
    botGet<PortfolioStats>('/api/stats'),
    creds ? getAccount(creds) : Promise.reject(new Error('no creds')),
    creds ? getOrders(creds, 'closed', 200) : Promise.reject(new Error('no creds')),
  ])

  const resolvedStats = stats.status === 'fulfilled' ? stats.value : demoStats()

  // Enrich stats with live Alpaca equity
  if (account.status === 'fulfilled') {
    const acc = account.value
    const livePnl  = parseFloat(acc.unrealized_pl) + parseFloat(acc.realized_pl ?? '0')
    const todayPnl = parseFloat(acc.equity) - parseFloat(acc.last_equity)
    if (!isNaN(livePnl))  resolvedStats.total_pnl = +livePnl.toFixed(2)
    if (!isNaN(todayPnl)) resolvedStats.today_pnl = +todayPnl.toFixed(2)
  }

  const resolvedTrades: TradeRecord[] = orders.status === 'fulfilled'
    ? orders.value.filter(o => parseFloat(o.filled_qty ?? '0') > 0).map(o => ({
        id:        o.id,
        ticker:    o.symbol,
        direction: o.side === 'buy' ? 'LONG' : 'SHORT',
        entry:     parseFloat(o.filled_avg_price ?? '0'),
        exit:      null, qty: parseInt(o.filled_qty),
        pnl: null, pnl_pct: null,
        opened_at: o.created_at, closed_at: o.filled_at,
        duration: null, status: 'closed' as const,
      }))
    : demoHistory()

  const live = pnl.status === 'fulfilled' || account.status === 'fulfilled'

  return {
    pnl:    pnl.status === 'fulfilled' ? pnl.value : demoPnL(),
    stats:  resolvedStats,
    trades: resolvedTrades,
    live,
  }
}

export default async function PnLPage() {
  const creds = await getAlpacaCreds()
  const { pnl, stats, trades, live } = await loadPnL(creds)
  return <PnLAnalytics pnl={pnl} stats={stats} trades={trades} live={live} />
}
