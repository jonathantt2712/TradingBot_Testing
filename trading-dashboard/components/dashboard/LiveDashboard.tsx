'use client'

import { useEffect, useState, useCallback } from 'react'
import { StatsCards }      from './StatsCards'
import { PnLChart }        from './PnLChart'
import { RegimeIndicator } from './RegimeIndicator'
import { SectorHeatmap }   from './SectorHeatmap'
import { PositionsTable }  from './PositionsTable'
import { cn } from '@/lib/utils'
import type { PortfolioStats, PnLPoint, RegimeInfo, SectorStat } from '@/types/trading'
import type { AlpacaPosition } from '@/lib/alpaca'

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
  initialRegime:    RegimeInfo
  initialSectors:   SectorStat[]
  initialPositions: AlpacaPosition[]
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
  initialRegime,
  initialSectors,
  initialPositions,
}: Props) {
  const [stats,          setStats]          = useState(initialStats)
  const [pnl,            setPnl]            = useState(initialPnl)
  const [regime,         setRegime]         = useState(initialRegime)
  const [sectors,        setSectors]        = useState(initialSectors)
  const [positions,      setPositions]      = useState(initialPositions)
  const [scanStats,      setScanStats]      = useState<ScanStats | null>(null)
  const [circuitBreaker, setCircuitBreaker] = useState<{ halted: boolean; reason?: string } | null>(null)

  // Fast: positions (30s)
  const refreshFast = useCallback(async () => {
    const [pos, sec] = await Promise.allSettled([
      fetch('/api/alpaca/positions').then(res => res.ok ? res.json() : null),
      fetch('/api/bot/sectors').then(res => res.ok ? res.json() : null),
    ])
    if (pos.status === 'fulfilled' && Array.isArray(pos.value)) setPositions(pos.value)
    if (sec.status === 'fulfilled' && Array.isArray(sec.value)) setSectors(sec.value)
  }, [])

  // Slow: stats, PnL, regime, scan-stats (5 min)
  const refreshSlow = useCallback(async () => {
    const [s, p, r, ss] = await Promise.allSettled([
      fetch('/api/bot/stats').then(res => res.ok ? res.json() : null),
      fetch('/api/bot/pnl').then(res => res.ok ? res.json() : null),
      fetch('/api/bot/regime').then(res => res.ok ? res.json() : null),
      fetch('/api/bot/scan-stats').then(res => res.ok ? res.json() : null),
    ])
    if (s.status === 'fulfilled' && s.value && !s.value.error) setStats(s.value)
    if (p.status === 'fulfilled' && Array.isArray(p.value))    setPnl(p.value)
    if (r.status === 'fulfilled' && r.value?.regime)           setRegime(r.value)
    if (ss.status === 'fulfilled' && ss.value) {
      setScanStats(ss.value)
      setCircuitBreaker(ss.value.circuit_breaker ?? null)
    }
  }, [])

  // Initial load of slow data
  useEffect(() => { refreshSlow() }, [refreshSlow])

  useEffect(() => {
    const fastId = setInterval(refreshFast, FAST_MS)
    return () => clearInterval(fastId)
  }, [refreshFast])

  useEffect(() => {
    const slowId = setInterval(refreshSlow, SLOW_MS)
    return () => clearInterval(slowId)
  }, [refreshSlow])

  return (
    <>
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

      <StatsCards stats={stats} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_220px]">
        <PnLChart data={pnl} />
        <RegimeIndicator regime={regime} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
        <SectorHeatmap sectors={sectors} />
        <PositionsTable positions={positions} />
      </div>
    </>
  )
}
