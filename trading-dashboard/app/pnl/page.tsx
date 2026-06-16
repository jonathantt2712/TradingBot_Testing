export const dynamic = 'force-dynamic'
import { PnLAnalytics } from '@/components/pnl/PnLAnalytics'
import { botGet }       from '@/lib/bot-api'
import { getAccount, getPortfolioHistory, type AlpacaCreds, type PortfolioHistory } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoPnL, demoStats, demoHistory } from '@/lib/api'
import type { PnLPoint, PortfolioStats, TradeRecord } from '@/types/trading'

/** Convert Alpaca portfolio history into the dashboard's PnLPoint series. */
function histToPnL(h: PortfolioHistory): PnLPoint[] {
  const base = h.base_value || h.equity[0] || 0
  return h.timestamp.map((ts, i) => ({
    date:           new Date(ts * 1000).toISOString().slice(0, 10),
    daily_pnl:      +(h.profit_loss[i] ?? 0).toFixed(2),
    cumulative_pnl: +((h.equity[i] ?? base) - base).toFixed(2),
    trade_count:    0,
  }))
}

async function loadPnL(creds: AlpacaCreds | null) {
  const [pnl, stats, account, history, botHistory] = await Promise.allSettled([
    botGet<PnLPoint[]>('/api/pnl'),
    botGet<PortfolioStats>('/api/stats'),
    creds ? getAccount(creds) : Promise.reject(new Error('no creds')),
    creds ? getPortfolioHistory(creds, '1A', '1D') : Promise.reject(new Error('no creds')),
    botGet<TradeRecord[]>('/api/history'),
  ])

  const resolvedStats = stats.status === 'fulfilled' ? stats.value : demoStats()

  // Enrich stats with real Alpaca account performance
  if (account.status === 'fulfilled') {
    const acc = account.value
    const todayPnl = parseFloat(acc.equity) - parseFloat(acc.last_equity)
    if (!isNaN(todayPnl)) resolvedStats.today_pnl = +todayPnl.toFixed(2)
    if (history.status === 'fulfilled') {
      const base = history.value.base_value
      const totalPnl = parseFloat(acc.equity) - base
      if (base > 0 && !isNaN(totalPnl)) resolvedStats.total_pnl = +totalPnl.toFixed(2)
    }
  }

  // Equity curve + daily P&L: prefer the real Alpaca account history, then the
  // bot's computed series, then demo data.
  const resolvedPnl: PnLPoint[] =
    history.status === 'fulfilled' && history.value.timestamp?.length
      ? histToPnL(history.value)
      : pnl.status === 'fulfilled'
        ? pnl.value
        : demoPnL()

  // Win/Loss + monthly need per-trade realized P&L. Alpaca's order list doesn't
  // expose paired entry/exit P&L, so use the bot's closed-trade history, which
  // records real pnl per trade.
  const resolvedTrades: TradeRecord[] =
    botHistory.status === 'fulfilled' && Array.isArray(botHistory.value) && botHistory.value.length
      ? botHistory.value
          .filter(t => t.status === 'closed' && t.pnl != null)
          .map(t => ({
            id:        t.id,
            ticker:    t.ticker,
            direction: t.direction,
            entry:     t.entry,
            exit:      t.exit ?? null,
            qty:       t.qty,
            pnl:       t.pnl,
            pnl_pct:   t.pnl_pct ?? null,
            opened_at: (t as any).executed_at ?? t.opened_at ?? '',
            closed_at: t.closed_at ?? null,
            duration:  t.duration ?? null,
            status:    'closed' as const,
          }))
      : demoHistory()

  const live = history.status === 'fulfilled' || account.status === 'fulfilled' || pnl.status === 'fulfilled'

  return { pnl: resolvedPnl, stats: resolvedStats, trades: resolvedTrades, live }
}

export default async function PnLPage() {
  const creds = await getAlpacaCreds()
  const { pnl, stats, trades, live } = await loadPnL(creds)
  return <PnLAnalytics pnl={pnl} stats={stats} trades={trades} live={live} />
}
