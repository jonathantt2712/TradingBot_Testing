export const dynamic = 'force-dynamic'

import { AccountBar }      from '@/components/dashboard/AccountBar'
import { HealthBanner, type HealthIssue } from '@/components/dashboard/HealthBanner'
import { LiveDashboard }   from '@/components/dashboard/LiveDashboard'
import { RegimeIndicator } from '@/components/dashboard/RegimeIndicator'
import { SectorHeatmap }   from '@/components/dashboard/SectorHeatmap'
import { RefreshButton }   from '@/components/layout/RefreshButton'
import {
  demoStats, demoPnL, demoRegime, demoSectors,
} from '@/lib/api'
import { getAccount, getPositions, getPortfolioHistory, getOrders, tradesFromOrders, type AlpacaCreds } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { botGet } from '@/lib/bot-api'
import { computeSharpe, computeMaxDD } from '@/lib/stats'
import { computeRegime } from '@/lib/regime'
import type { PortfolioStats, PnLPoint, RegimeInfo, SectorStat } from '@/types/trading'
import type { AlpacaAccount } from '@/lib/alpaca'

async function loadDashboard(creds: AlpacaCreds | null) {
  const [account, positions, history, orders, stats, regime, sectors, health, scanStats] = await Promise.allSettled([
    creds ? getAccount(creds) : Promise.reject(new Error('no creds')),
    creds ? getPositions(creds) : Promise.reject(new Error('no creds')),
    creds ? getPortfolioHistory(creds, '1A', '1D') : Promise.reject(new Error('no creds')),
    creds ? getOrders(creds, 'closed', 200) : Promise.reject(new Error('no creds')),
    botGet<PortfolioStats>('/api/stats'),
    creds ? computeRegime(creds) : Promise.reject(new Error('no creds')),
    botGet<SectorStat[]>('/api/sectors'),
    botGet<{ trading?: { mode_label?: string; execute_live?: boolean; paper_mode?: boolean }; issues?: HealthIssue[] }>('/api/health'),
    botGet<Record<string, unknown>>('/api/scan-stats'),
  ])

  const accountErrorDetail = account.status === 'rejected'
    ? String((account.reason as Error)?.message ?? account.reason)
    : null

  const resolvedStats: PortfolioStats = stats.status === 'fulfilled' ? stats.value : demoStats()
  if (account.status === 'fulfilled') {
    const acc = account.value
    const todayPnl = parseFloat(acc.equity) - parseFloat(acc.last_equity)
    if (!isNaN(todayPnl)) resolvedStats.today_pnl = +todayPnl.toFixed(2)

    // Real account total P&L = current equity − account value at start of period.
    // (Alpaca's /v2/account does NOT return unrealized_pl/realized_pl, so we use
    // portfolio history's base_value, which is the genuine account baseline.)
    if (history.status === 'fulfilled') {
      const base = history.value.base_value
      const totalPnl = parseFloat(acc.equity) - base
      if (base > 0 && !isNaN(totalPnl)) resolvedStats.total_pnl = +totalPnl.toFixed(2)
    }
  }
  if (positions.status === 'fulfilled') {
    resolvedStats.open_positions = positions.value.length
  }

  // Build chart data + Sharpe/MaxDD from Alpaca history (no bot dependency)
  let resolvedPnl: PnLPoint[] = demoPnL()
  if (history.status === 'fulfilled') {
    const hist = history.value
    const base = hist.base_value || hist.equity?.find((e: number) => e > 0) || 0
    const pts: PnLPoint[] = (hist.timestamp ?? [])
      .map((ts: number, i: number) => ({
        date:           new Date(ts * 1000).toISOString().slice(0, 10),
        daily_pnl:      +(hist.profit_loss?.[i] ?? 0).toFixed(2),
        cumulative_pnl: base > 0 ? +((hist.equity?.[i] ?? 0) - base).toFixed(2) : +(hist.profit_loss?.[i] ?? 0).toFixed(2),
        trade_count:    0,
        equity:         +(hist.equity?.[i] ?? 0).toFixed(2),
      }))
      .filter((p: PnLPoint) => (p.equity ?? 0) > 0)

    if (pts.length > 0) {
      // Append today as a live data point if the last completed day is before today
      const today = new Date().toISOString().slice(0, 10)
      if (pts.at(-1)!.date < today && account.status === 'fulfilled') {
        const eq = parseFloat(account.value.equity)
        if (eq > 0) {
          const prevEq = pts.at(-1)!.equity ?? 0
          pts.push({
            date:           today,
            daily_pnl:      +(eq - prevEq).toFixed(2),
            cumulative_pnl: base > 0 ? +(eq - base).toFixed(2) : 0,
            trade_count:    0,
            equity:         +eq.toFixed(2),
          })
        }
      }

      resolvedPnl = pts
      const sharpe = computeSharpe(pts)
      if (sharpe !== null) resolvedStats.sharpe_ratio = sharpe
      const maxDD = computeMaxDD(pts)
      if (maxDD !== null) resolvedStats.max_drawdown = maxDD
    }
  }

  // Win rate + trade count from Alpaca closed orders (bot default is 0)
  if (orders.status === 'fulfilled' && orders.value.length > 0) {
    const trades = tradesFromOrders(orders.value)
    const closed = trades.filter(t => t.pnl != null)
    if (closed.length > 0) {
      const wins = closed.filter(t => (t.pnl ?? 0) > 0).length
      resolvedStats.win_rate     = +(wins / closed.length * 100).toFixed(1)
      resolvedStats.total_trades = closed.length
    }
  }

  const tradingMode = health.status === 'fulfilled'
    ? (health.value?.trading?.mode_label ?? 'DRY RUN')
    : 'DRY RUN'

  const issues: HealthIssue[] = health.status === 'fulfilled'
    ? (health.value?.issues ?? [])
    : []

  return {
    stats:         resolvedStats,
    account:       account.status === 'fulfilled' ? account.value : null as AlpacaAccount | null,
    accountError:  accountErrorDetail,
    pnl:           resolvedPnl,
    regime:        regime.status    === 'fulfilled' ? regime.value    : demoRegime(),
    sectors:       sectors.status   === 'fulfilled' ? sectors.value   : demoSectors(),
    positions:     positions.status === 'fulfilled' ? positions.value : [],
    scanStats:     scanStats.status === 'fulfilled' ? scanStats.value : null,
    live:          account.status === 'fulfilled',
    tradingMode,
    issues,
  }
}

export default async function DashboardPage() {
  const creds = await getAlpacaCreds()
  const { stats, account, accountError, pnl, regime, sectors, positions, scanStats, live, tradingMode, issues } = await loadDashboard(creds)

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 md:space-y-6 max-w-[1400px]">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg md:text-xl font-bold text-primary">Dashboard</h1>
          <p className="text-xs text-muted mt-0.5 hidden sm:block">
            {new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {live
            ? <span className="flex items-center gap-1.5 text-xs text-bull"><span className="h-1.5 w-1.5 rounded-full bg-bull animate-pulse-slow" />Live</span>
            : <span className="flex items-center gap-1.5 text-xs text-caution"><span className="h-1.5 w-1.5 rounded-full bg-caution" />Demo</span>
          }
          <HealthBanner issues={issues} />
          <RefreshButton />
        </div>
      </div>

      {/* Two-column layout — right column stretches to match left height */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_240px]">
        <div className="space-y-4">
          <AccountBar account={account} error={accountError} tradingMode={tradingMode} />
          <LiveDashboard
            initialStats={stats}
            initialPnl={pnl}
            initialSectors={sectors}
            initialPositions={positions}
            initialScanStats={scanStats as any}
          />
        </div>
        {/* Right column: Regime at top, Sector Heat fills remaining height */}
        <div className="hidden lg:flex flex-col gap-4">
          <RegimeIndicator regime={regime} />
          <div className="flex-1 min-h-0">
            <SectorHeatmap sectors={sectors} />
          </div>
        </div>
      </div>
    </div>
  )
}
