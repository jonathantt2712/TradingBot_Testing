export const dynamic = 'force-dynamic'
import { PnLAnalytics } from '@/components/pnl/PnLAnalytics'
import { botGet }       from '@/lib/bot-api'
import { getAccount, getPortfolioHistory, getOrders, tradesFromOrders, mergeTrades, type AlpacaCreds, type PortfolioHistory } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoPnL, demoStats } from '@/lib/api'
import type { PnLPoint, PortfolioStats, TradeRecord } from '@/types/trading'

/** Convert Alpaca portfolio history into the dashboard's PnLPoint series.
 *  Filters out zero-equity points — Alpaca returns equity=0 for non-trading
 *  hours/weekends which creates false massive drawdowns in the chart. */
function histToPnL(h: PortfolioHistory): PnLPoint[] {
  const base = h.base_value || h.equity.find(e => e > 0) || 0
  return h.timestamp
    .map((ts, i) => ({ ts, equity: h.equity[i] ?? 0, pl: h.profit_loss[i] ?? 0 }))
    .filter(p => p.equity > 0)
    .map(p => ({
      date:           new Date(p.ts * 1000).toISOString().slice(0, 10),
      daily_pnl:      +p.pl.toFixed(2),
      cumulative_pnl: +(p.equity - base).toFixed(2),
      trade_count:    0,
      equity:         +p.equity.toFixed(2),
    }))
}

function computeSharpe(pnl: PnLPoint[]): number | null {
  // Prefer equity-based daily returns (% change) — works regardless of account size.
  const equities = pnl.map(p => p.equity).filter((e): e is number => e != null && e > 0)
  let vals: number[]

  if (equities.length >= 3) {
    // Daily return = (equity[i] - equity[i-1]) / equity[i-1]
    vals = []
    for (let i = 1; i < equities.length; i++) {
      vals.push((equities[i] - equities[i - 1]) / equities[i - 1])
    }
  } else {
    // Fallback: dollar daily P&L (less reliable for small accounts)
    vals = pnl.map(p => p.daily_pnl).filter(v => isFinite(v) && v !== 0)
  }

  if (vals.length < 2) return null
  const mean     = vals.reduce((s, v) => s + v, 0) / vals.length
  const variance = vals.reduce((s, v) => s + (v - mean) ** 2, 0) / (vals.length - 1)
  const std      = Math.sqrt(variance)
  if (std === 0) return null
  return +(mean / std * Math.sqrt(252)).toFixed(2)
}

async function loadPnL(creds: AlpacaCreds | null) {
  const [pnl, stats, account, history, botHistory, fillsResult, attrResult, mcResult, regimeResult] = await Promise.allSettled([
    botGet<PnLPoint[]>('/api/pnl'),
    botGet<PortfolioStats>('/api/stats'),
    creds ? getAccount(creds) : Promise.reject(new Error('no creds')),
    creds ? getPortfolioHistory(creds, '3M', '1D') : Promise.reject(new Error('no creds')),
    botGet<TradeRecord[]>('/api/history'),
    creds ? getOrders(creds, 'closed', 200) : Promise.reject(new Error('no creds')),
    botGet<Record<string, { wins: number; losses: number; total: number; win_rate: number; total_pnl: number }>>('/api/agent-attribution'),
    botGet<{ actual_win_rate: number; ci_95_lo: number; ci_95_hi: number; pnl_p5: number; pnl_p50: number; pnl_p95: number; n_trades: number; skill_signal: boolean; error?: string }>('/api/monte-carlo'),
    botGet<Record<string, { trades: number; wins: number; win_rate: number; total_pnl: number; avg_pnl: number }>>('/api/regime-performance'),
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

  // Per-trade P&L: bot history (has exact pnl) merged with FIFO-matched fills
  const botTrades:  TradeRecord[] = botHistory.status   === 'fulfilled' ? botHistory.value.filter(t => t.pnl != null) : []
  const fillTrades: TradeRecord[] = fillsResult.status  === 'fulfilled' ? tradesFromOrders(fillsResult.value)          : []
  const resolvedTrades = mergeTrades(botTrades, fillTrades)

  // Override win_rate and total_trades from real trades (bot stats may use demo fallback)
  const closedWithPnl = resolvedTrades.filter(t => t.pnl != null)
  if (closedWithPnl.length > 0) {
    const wins = closedWithPnl.filter(t => (t.pnl ?? 0) > 0).length
    resolvedStats.win_rate     = +(wins / closedWithPnl.length * 100).toFixed(1)
    resolvedStats.total_trades = closedWithPnl.length
  }

  // Compute Sharpe ratio from real daily P&L series (mean/std * sqrt(252))
  const sharpe = computeSharpe(resolvedPnl)
  if (sharpe !== null) resolvedStats.sharpe_ratio = sharpe

  const live = history.status === 'fulfilled' || account.status === 'fulfilled' || pnl.status === 'fulfilled'
  const attribution  = attrResult.status   === 'fulfilled' ? attrResult.value   : undefined
  const monteCarlo   = mcResult.status     === 'fulfilled' ? mcResult.value     : undefined
  const regimePerf   = regimeResult.status === 'fulfilled' ? regimeResult.value : undefined

  return { pnl: resolvedPnl, stats: resolvedStats, trades: resolvedTrades, live, attribution, monteCarlo, regimePerf }
}

export default async function PnLPage() {
  const creds = await getAlpacaCreds()
  const { pnl, stats, trades, live, attribution, monteCarlo, regimePerf } = await loadPnL(creds)
  return <PnLAnalytics pnl={pnl} stats={stats} trades={trades} live={live} attribution={attribution} monteCarlo={monteCarlo} regimePerf={regimePerf} />
}
