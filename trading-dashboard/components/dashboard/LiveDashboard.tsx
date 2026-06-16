'use client'

import { useEffect, useState, useCallback } from 'react'
import { StatsCards }      from './StatsCards'
import { PnLChart }        from './PnLChart'
import { RegimeIndicator } from './RegimeIndicator'
import { SectorHeatmap }   from './SectorHeatmap'
import { PositionsTable }  from './PositionsTable'
import type { PortfolioStats, PnLPoint, RegimeInfo, SectorStat } from '@/types/trading'
import type { AlpacaPosition } from '@/lib/alpaca'

const REFRESH_MS = 30_000

interface Props {
  initialStats:     PortfolioStats
  initialPnl:       PnLPoint[]
  initialRegime:    RegimeInfo
  initialSectors:   SectorStat[]
  initialPositions: AlpacaPosition[]
}

export function LiveDashboard({
  initialStats,
  initialPnl,
  initialRegime,
  initialSectors,
  initialPositions,
}: Props) {
  const [stats,     setStats]     = useState(initialStats)
  const [pnl,       setPnl]       = useState(initialPnl)
  const [regime,    setRegime]    = useState(initialRegime)
  const [sectors,   setSectors]   = useState(initialSectors)
  const [positions, setPositions] = useState(initialPositions)

  const refresh = useCallback(async () => {
    const [s, p, r, sec, pos] = await Promise.allSettled([
      fetch('/api/bot/stats').then(res => res.ok ? res.json() : null),
      fetch('/api/alpaca/portfolio-history').then(res => res.ok ? res.json() : null),
      fetch('/api/bot/regime').then(res => res.ok ? res.json() : null),
      fetch('/api/bot/sectors').then(res => res.ok ? res.json() : null),
      fetch('/api/alpaca/positions').then(res => res.ok ? res.json() : null),
    ])

    if (s.status === 'fulfilled' && s.value && !s.value.error) setStats(s.value)
    if (p.status === 'fulfilled' && Array.isArray(p.value))    setPnl(p.value)
    if (r.status === 'fulfilled' && r.value?.regime)           setRegime(r.value)
    if (sec.status === 'fulfilled' && Array.isArray(sec.value)) setSectors(sec.value)
    if (pos.status === 'fulfilled' && Array.isArray(pos.value)) setPositions(pos.value)
  }, [])

  useEffect(() => {
    const id = setInterval(refresh, REFRESH_MS)
    return () => clearInterval(id)
  }, [refresh])

  return (
    <>
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
