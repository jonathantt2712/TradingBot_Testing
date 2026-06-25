'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import dynamic from 'next/dynamic'
import { StatsCards }     from './StatsCards'
import { PositionsTable } from './PositionsTable'
import { cn } from '@/lib/utils'
import type { PortfolioStats, PnLPoint, SectorStat } from '@/types/trading'
import type { AlpacaPosition } from '@/lib/alpaca'

// Charts pull in recharts (~115 kB). Defer it so the dashboard shell (stats,
// regime, positions) paints first and the chart hydrates client-side after.
const PnLChart = dynamic(() => import('./PnLChart').then(m => m.PnLChart), {
  ssr: false,
  loading: () => <div className="h-64 w-full animate-pulse rounded-lg bg-bg-elev" />,
})

// Positions and recommendations: refresh every 30s
const FAST_MS = 30_000
// Regime, scan-stats, stats and PnL: refresh every 5 minutes
const SLOW_MS = 300_000

interface ScanStats {
  market_open?:     boolean | null
  scans_today?:     number
  tickers_scanned?: number
  recs_generated?:  number
  scan_errors?:     number
  last_scan_at?:    string | null
  circuit_breaker?: { halted: boolean; reason?: string } | null
}

interface Props {
  initialStats:     PortfolioStats
  initialPnl:       PnLPoint[]
  initialSectors:   SectorStat[]
  initialPositions: AlpacaPosition[]
  initialScanStats: ScanStats | null
}

function relativeTime(iso: string): string {
  const mins = Math.floor((Date.now() - new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  return `${Math.floor(mins / 60)}h ago`
}

export function LiveDashboard({
  initialStats,
  initialPnl,
  initialSectors,
  initialPositions,
  initialScanStats,
}: Props) {
  const [stats,          setStats]          = useState(initialStats)
  const [pnl,            setPnl]            = useState(initialPnl)
  const [sectors,        setSectors]        = useState(initialSectors)
  const [positions,      setPositions]      = useState(initialPositions)
  const [scanStats,      setScanStats]      = useState<ScanStats | null>(initialScanStats)
  const [circuitBreaker, setCircuitBreaker] = useState<{ halted: boolean; reason?: string } | null>(
    initialScanStats?.circuit_breaker ?? null
  )

  // Tracks symbols the user has closed — persists across refreshes until
  // Alpaca confirms the position is truly gone from their end.
  const pendingClose = useRef<Set<string>>(new Set())

  const applyPositions = useCallback((raw: AlpacaPosition[]) => {
    const filtered = raw.filter(p => !pendingClose.current.has(p.symbol))
    // Once Alpaca stops returning a symbol, remove it from pendingClose
    const alpacaSymbols = new Set(raw.map(p => p.symbol))
    for (const sym of pendingClose.current) {
      if (!alpacaSymbols.has(sym)) pendingClose.current.delete(sym)
    }
    return filtered
  }, [])

  // Fast: positions + stats (30s)
  const refreshFast = useCallback(async () => {
    const [pos, sec, s] = await Promise.allSettled([
      fetch('/api/alpaca/positions').then(res => res.ok ? res.json() : null),
      fetch('/api/bot/sectors').then(res => res.ok ? res.json() : null),
      fetch('/api/bot/stats').then(res => res.ok ? res.json() : null),
    ])
    const rawPositions  = pos.status === 'fulfilled' && Array.isArray(pos.value) ? pos.value as AlpacaPosition[] : null
    const newPositions  = rawPositions ? applyPositions(rawPositions) : null
    if (newPositions !== null)                                         setPositions(newPositions)
    if (sec.status === 'fulfilled' && Array.isArray(sec.value))       setSectors(sec.value)
    if (s.status   === 'fulfilled' && s.value && !s.value.error) {
      setStats(prev => {
        const next = { ...s.value }
        next.open_positions = newPositions !== null ? newPositions.length : s.value.open_positions
        // Preserve Alpaca-computed values when the bot returns its 0 defaults.
        // These are set server-side from the equity curve and the bot has no
        // access to Alpaca history, so its values are always less accurate.
        if (!next.sharpe_ratio  && prev.sharpe_ratio)  next.sharpe_ratio  = prev.sharpe_ratio
        if (!next.max_drawdown  && prev.max_drawdown)  next.max_drawdown  = prev.max_drawdown
        if (!next.total_pnl     && prev.total_pnl)     next.total_pnl     = prev.total_pnl
        if (!next.today_pnl     && prev.today_pnl)     next.today_pnl     = prev.today_pnl
        return next
      })
    }
  }, [applyPositions])

  // Slow: scan-stats only (5 min).
  // PnL is historical daily data — SSR loads it fresh on each page load,
  // no need to poll and risk overwriting with empty/wrong data mid-session.
  const refreshSlow = useCallback(async () => {
    const ss = await fetch('/api/bot/scan-stats').then(r => r.ok ? r.json() : null).catch(() => null)
    if (ss) {
      setScanStats(ss)
      setCircuitBreaker(ss.circuit_breaker ?? null)
    }
  }, [])

  // ⚠️  DO NOT add an immediate useEffect(() => { refreshFast/Slow() }, [...]) here.
  //
  // This component receives all its data as fresh SSR props (fetched server-side
  // on every page load). Firing a client-side fetch on mount overwrites those
  // Alpaca-computed values (Sharpe, Max DD, total P&L, regime) with the bot's
  // cached/default values ~500ms later, causing a visible flicker.
  //
  // Polling starts AFTER the first interval so SSR data is never clobbered.
  // If you need data not covered by SSR, add it to loadDashboard() in app/page.tsx
  // and pass it as an initial* prop — then use skipInitialRun: true in usePolling.
  useEffect(() => {
    const fastId = setInterval(refreshFast, FAST_MS)
    return () => clearInterval(fastId)
  }, [refreshFast])

  useEffect(() => {
    const slowId = setInterval(refreshSlow, SLOW_MS)
    return () => clearInterval(slowId)
  }, [refreshSlow])

  function handleClosed(symbol: string) {
    // Mark as pending close — survives every future Alpaca refresh
    pendingClose.current.add(symbol)
    // Remove immediately from every piece of state that mentions positions
    setPositions(prev => prev.filter(p => p.symbol !== symbol))
    setStats(prev => ({ ...prev, open_positions: Math.max(0, (prev.open_positions ?? 1) - 1) }))
  }

  return (
    <>
      {/* Circuit breaker — full width, above everything */}
      {circuitBreaker?.halted && (
        <div className="rounded-xl border border-bear/40 bg-bear/10 px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-bear animate-pulse" />
            <span className="text-sm font-semibold text-bear">Circuit Breaker Active</span>
            <span className="text-xs text-muted">{circuitBreaker.reason?.replace(/_/g, ' ')}</span>
          </div>
          <button
            onClick={async () => {
              await fetch('/api/bot/reset-circuit-breaker', { method: 'POST' })
              setCircuitBreaker(null)
            }}
            className="text-xs text-muted hover:text-primary transition-colors"
          >
            Reset
          </button>
        </div>
      )}

      {/* Scan stats strip */}
      {scanStats && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-lg border border-bg-border bg-bg-card px-4 py-2 text-xs text-muted">
          <span className="flex items-center gap-1.5 font-medium">
            <span className={cn('h-2 w-2 rounded-full', scanStats.market_open ? 'bg-bull' : 'bg-bear')} />
            {scanStats.market_open ? 'Market Open' : 'Market Closed'}
          </span>
          {scanStats.last_scan_at && (
            <span>Last scan: <span className="text-subtle">{relativeTime(scanStats.last_scan_at)}</span></span>
          )}
          <span>Scans today: <span className="text-subtle">{scanStats.scans_today ?? '—'}</span></span>
          <span>Tickers scanned: <span className="text-subtle">{scanStats.tickers_scanned ?? '—'}</span></span>
          {(scanStats.scan_errors ?? 0) > 0 && (
            <span className="text-bear font-semibold">Errors: {scanStats.scan_errors}</span>
          )}
        </div>
      )}

      <StatsCards stats={stats} />

      <PnLChart data={pnl} />

      <PositionsTable positions={positions} onClosed={handleClosed} />
    </>
  )
}
