export const dynamic = 'force-dynamic'

import { AccountBar }     from '@/components/dashboard/AccountBar'
import { LiveDashboard }  from '@/components/dashboard/LiveDashboard'
import { RefreshButton }  from '@/components/layout/RefreshButton'
import {
  demoStats, demoPnL, demoRegime, demoSectors,
} from '@/lib/api'
import { getAccount, getPositions, type AlpacaCreds } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { botGet } from '@/lib/bot-api'
import type { PortfolioStats, PnLPoint, RegimeInfo, SectorStat } from '@/types/trading'
import type { AlpacaAccount } from '@/lib/alpaca'

async function loadDashboard(creds: AlpacaCreds | null) {
  const [account, positions, stats, pnl, regime, sectors] = await Promise.allSettled([
    creds ? getAccount(creds) : Promise.reject(new Error('no creds')),
    creds ? getPositions(creds) : Promise.reject(new Error('no creds')),
    botGet<PortfolioStats>('/api/stats'),
    botGet<PnLPoint[]>('/api/pnl'),
    botGet<RegimeInfo>('/api/regime'),
    botGet<SectorStat[]>('/api/sectors'),
  ])

  if (account.status === 'rejected') {
    console.error('getAccount failed:', account.reason)
    if (creds) console.error('getAccount creds used:', { paper: creds.paper, keyId: `${creds.keyId.slice(0, 4)}...${creds.keyId.slice(-4)}` })
  }

  const accountErrorDetail = account.status === 'rejected'
    ? `${String((account.reason as Error)?.message ?? account.reason)}${creds ? ` (paper=${creds.paper}, keyId=${creds.keyId.slice(0, 4)}...${creds.keyId.slice(-4)})` : ' (no creds on session)'}`
    : null

  const resolvedStats: PortfolioStats = stats.status === 'fulfilled' ? stats.value : demoStats()
  if (account.status === 'fulfilled') {
    const acc = account.value
    const livePnl  = parseFloat(acc.unrealized_pl) + parseFloat(acc.realized_pl ?? '0')
    const todayPnl = parseFloat(acc.equity) - parseFloat(acc.last_equity)
    if (!isNaN(livePnl))  resolvedStats.total_pnl = +livePnl.toFixed(2)
    if (!isNaN(todayPnl)) resolvedStats.today_pnl = +todayPnl.toFixed(2)
  }
  if (positions.status === 'fulfilled') {
    resolvedStats.open_positions = positions.value.length
  }

  return {
    stats:        resolvedStats,
    account:      account.status === 'fulfilled' ? account.value : null as AlpacaAccount | null,
    accountError: accountErrorDetail,
    pnl:          pnl.status      === 'fulfilled' ? pnl.value      : demoPnL(),
    regime:       regime.status   === 'fulfilled' ? regime.value   : demoRegime(),
    sectors:      sectors.status  === 'fulfilled' ? sectors.value  : demoSectors(),
    positions:    positions.status === 'fulfilled' ? positions.value : [],
    live:         account.status === 'fulfilled',
  }
}

export default async function DashboardPage() {
  const creds = await getAlpacaCreds()
  const { stats, account, accountError, pnl, regime, sectors, positions, live } = await loadDashboard(creds)

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

      <AccountBar account={account} error={accountError} />

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
