'use client'

import React, { useEffect, useState, useCallback } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react'
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

  // Symbols the user closed this session — shown with "pending close" badge
  // until Alpaca confirms they're gone on next refresh.
  const [pendingClose,   setPendingClose]   = useState<Set<string>>(new Set())
  const [alertsOpen,     setAlertsOpen]     = useState(false)

  // Fast: positions + stats (30s)
  const refreshFast = useCallback(async () => {
    const [pos, sec, s] = await Promise.allSettled([
      fetch('/api/alpaca/positions').then(res => res.ok ? res.json() : null),
      fetch('/api/bot/sectors').then(res => res.ok ? res.json() : null),
      fetch('/api/bot/stats').then(res => res.ok ? res.json() : null),
    ])
    const rawPositions = pos.status === 'fulfilled' && Array.isArray(pos.value) ? pos.value as AlpacaPosition[] : null
    if (rawPositions !== null) {
      // Clear pendingClose for symbols Alpaca no longer reports (confirmed closed)
      setPendingClose(prev => {
        const next = new Set(prev)
        for (const sym of prev) {
          if (!rawPositions.some(p => p.symbol === sym)) next.delete(sym)
        }
        return next.size === prev.size ? prev : next
      })
      setPositions(rawPositions)
    }
    if (sec.status === 'fulfilled' && Array.isArray(sec.value))       setSectors(sec.value)
    if (s.status   === 'fulfilled' && s.value && !s.value.error) {
      const newStats = { ...s.value }
      newStats.open_positions = rawPositions !== null ? rawPositions.length : s.value.open_positions
      setStats(newStats)
    }
  }, [])

  // Slow: PnL chart, regime, scan-stats (5 min)
  const refreshSlow = useCallback(async () => {
    const [p, r, ss] = await Promise.allSettled([
      fetch('/api/bot/pnl').then(res => res.ok ? res.json() : null),
      fetch('/api/bot/regime').then(res => res.ok ? res.json() : null),
      fetch('/api/bot/scan-stats').then(res => res.ok ? res.json() : null),
    ])
    if (p.status  === 'fulfilled' && Array.isArray(p.value))  setPnl(p.value)
    if (r.status  === 'fulfilled' && r.value?.regime)          setRegime(r.value)
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

  function handleClosed(symbol: string) {
    setPendingClose(prev => new Set([...prev, symbol]))
  }

  // Build issues list from scan stats + circuit breaker
  const issues: { id: string; label: string; detail?: string; action?: React.ReactNode }[] = []
  if (circuitBreaker?.halted) {
    issues.push({
      id: 'cb',
      label: 'Circuit Breaker Active',
      detail: circuitBreaker.reason?.replace(/_/g, ' '),
      action: (
        <button
          onClick={async () => {
            await fetch('/api/bot/reset-circuit-breaker', { method: 'POST' })
            setCircuitBreaker(null)
          }}
          className="rounded-md border border-bear/30 px-2 py-0.5 text-[10px] text-bear hover:bg-bear/10 transition-colors"
        >
          Reset
        </button>
      ),
    })
  }
  if ((scanStats?.scan_errors ?? 0) > 0) {
    issues.push({
      id: 'scan-err',
      label: `${scanStats!.scan_errors} scan error${(scanStats!.scan_errors ?? 0) > 1 ? 's' : ''}`,
      detail: scanStats?.last_scan_at ? `Last scan: ${relativeTime(scanStats.last_scan_at)}` : undefined,
    })
  }
  if (scanStats && !scanStats.market_open && (scanStats.scans_today ?? 0) === 0) {
    issues.push({ id: 'no-scans', label: 'No scans today', detail: 'Market may be closed or bot offline' })
  }

  return (
    <>
      {/* Compact alerts bar — only shown when there are issues */}
      {issues.length > 0 && (
        <div className={cn(
          'rounded-lg border transition-colors',
          issues.some(i => i.id === 'cb') ? 'border-bear/40 bg-bear/5' : 'border-caution/40 bg-caution/5',
        )}>
          {/* Header — always visible */}
          <button
            onClick={() => setAlertsOpen(v => !v)}
            className="flex w-full items-center gap-2 px-4 py-2 text-left"
          >
            <AlertTriangle className={cn(
              'h-3.5 w-3.5 shrink-0',
              issues.some(i => i.id === 'cb') ? 'text-bear' : 'text-caution',
            )} />
            <span className={cn(
              'text-xs font-semibold',
              issues.some(i => i.id === 'cb') ? 'text-bear' : 'text-caution',
            )}>
              {issues.length === 1 ? issues[0].label : `${issues.length} התראות`}
            </span>
            {alertsOpen
              ? <ChevronUp className="ml-auto h-3.5 w-3.5 text-muted" />
              : <ChevronDown className="ml-auto h-3.5 w-3.5 text-muted" />
            }
          </button>

          {/* Expanded detail */}
          {alertsOpen && (
            <div className="border-t border-bg-border px-4 py-3 space-y-2">
              {issues.map(issue => (
                <div key={issue.id} className="flex items-start justify-between gap-3">
                  <div>
                    <span className="text-xs font-semibold text-primary">{issue.label}</span>
                    {issue.detail && (
                      <p className="text-[11px] text-muted mt-0.5">{issue.detail}</p>
                    )}
                  </div>
                  {issue.action}
                </div>
              ))}
              {scanStats && (
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-bg-border pt-3 text-[11px] text-muted">
                  <span className="flex items-center gap-1.5">
                    <span className={cn('h-1.5 w-1.5 rounded-full', scanStats.market_open ? 'bg-bull' : 'bg-muted')} />
                    {scanStats.market_open ? 'Market Open' : 'Market Closed'}
                  </span>
                  {scanStats.last_scan_at && <span>Last scan: {relativeTime(scanStats.last_scan_at)}</span>}
                  <span>Scans today: {scanStats.scans_today ?? '—'}</span>
                  <span>Tickers: {scanStats.tickers_scanned ?? '—'}</span>
                  <span>Signals generated: {scanStats.recs_generated ?? '—'}</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <StatsCards stats={stats} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_220px]">
        <PnLChart data={pnl} />
        <RegimeIndicator regime={regime} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
        <SectorHeatmap sectors={sectors} />
        <PositionsTable positions={positions} onClosed={handleClosed} pendingClose={pendingClose} />
      </div>
    </>
  )
}
