export const dynamic = 'force-dynamic'

import { AccountBar }     from '@/components/dashboard/AccountBar'
import { HealthBanner, type HealthIssue } from '@/components/dashboard/HealthBanner'
import { LiveDashboard }  from '@/components/dashboard/LiveDashboard'
import { RefreshButton }  from '@/components/layout/RefreshButton'
import {
  demoStats, demoPnL, demoRegime, demoSectors,
} from '@/lib/api'
import { getAccount, getPositions, getPortfolioHistory, type AlpacaCreds } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { botGet } from '@/lib/bot-api'
import type { PortfolioStats, PnLPoint, RegimeInfo, SectorStat } from '@/types/trading'
import type { AlpacaAccount } from '@/lib/alpaca'

async function loadDashboard(creds: AlpacaCreds | null) {
  const [account, positions, history, stats, pnl, regime, sectors, health] = await Promise.allSettled([
    creds ? getAccount(creds) : Promise.reject(new Error('no creds')),
    creds ? getPositions(creds) : Promise.reject(new Error('no creds')),
    creds ? getPortfolioHistory(creds, '1A', '1D') : Promise.reject(new Error('no creds')),
    botGet<PortfolioStats>('/api/stats'),
    botGet<PnLPoint[]>('/api/pnl'),
    botGet<RegimeInfo>('/api/regime'),
    botGet<SectorStat[]>('/api/sectors'),
    botGet<{ trading?: { mode_label?: string; execute_live?: boolean; paper_mode?: boolean }; issues?: HealthIssue[] }>('/api/health'),
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

  const tradingMode = health.status === 'fulfilled'
    ? (health.value?.trading?.mode_label ?? 'DRY RUN')
    : 'DRY RUN'

  const issues: HealthIssue[] = health.status === 'fulfilled'
    ? (health.value?.issues ?? [])
    : []

  return {
    stats:        resolvedStats,
    account:      account.status === 'fulfilled' ? account.value : null as AlpacaAccount | null,
    accountError: accountErrorDetail,
    pnl:          pnl.status      === 'fulfilled' ? pnl.value      : demoPnL(),
    regime:       regime.status   === 'fulfilled' ? regime.value   : demoRegime(),
    sectors:      sectors.status  === 'fulfilled' ? sectors.value  : demoSectors(),
    positions:    positions.status === 'fulfilled' ? positions.value : [],
    live:         account.status === 'fulfilled',
    tradingMode,
    issues,
  }
}

export default async function DashboardPage() {
  const creds = await getAlpacaCreds()
  const { stats, account, accountError, pnl, regime, sectors, positions, live, tradingMode, issues } = await loadDashboard(creds)

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
          <RefreshButton />
        </div>
      </div>

      <AccountBar account={account} error={accountError} tradingMode={tradingMode} />

      <HealthBanner issues={issues} />

      <LiveDashboard
        initialStats={stats}
        initialPnl={pnl}
        initialRegime={regime}
        initialSectors={sectors}
        initialPositions={positions}
      />
    </div>
  )
}
