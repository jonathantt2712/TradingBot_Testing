'use client'
import { useState, useEffect, useCallback } from 'react'
import { RefreshCw, Wifi, WifiOff, Clock } from 'lucide-react'
import { demoRegime, demoRecommendations, api } from '@/lib/api'
import { TickerBriefCard } from '@/components/agents/TickerBriefCard'
import { cn } from '@/lib/utils'
import { regimeLabel, regimeColor } from '@/lib/utils'
import type { TradeRecommendation, RegimeInfo } from '@/types/trading'

const REFRESH_MS = 30_000

function relativeTime(iso: string | null): string {
  if (!iso) return 'never'
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000)
  if (mins < 1) return 'just now'
  if (mins === 1) return '1m ago'
  if (mins < 60) return `${mins}m ago`
  return `${Math.floor(mins / 60)}h ago`
}

export default function AgentsPage() {
  const [recommendations, setRecommendations] = useState<TradeRecommendation[]>(demoRecommendations())
  const [regime,          setRegime]          = useState<RegimeInfo>(demoRegime())
  const [live,            setLive]            = useState(false)
  const [loading,         setLoading]         = useState(false)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [recs, reg] = await Promise.allSettled([api.recommendations(), api.regime()])
      if (recs.status === 'fulfilled') setRecommendations(recs.value)
      if (reg.status === 'fulfilled')  setRegime(reg.value)
      setLive(recs.status === 'fulfilled' && reg.status === 'fulfilled')
    } catch {
      setLive(false)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, REFRESH_MS)
    return () => clearInterval(id)
  }, [fetchData])

  const lastUpdated = [regime.timestamp, ...recommendations.map(r => r.timestamp)]
    .filter(Boolean)
    .sort()
    .at(-1) ?? null

  const sorted = [...recommendations].sort((a, b) => b.composite_score - a.composite_score)

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 max-w-[900px] mx-auto">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-lg font-bold text-primary">Agents</h1>
          <span className={cn('rounded-full border px-3 py-0.5 text-xs font-bold', regimeColor(regime.regime))}>
            {regimeLabel(regime.regime)}
          </span>
          <span className="flex items-center gap-1 text-xs text-muted">
            <Clock className="h-3 w-3" />
            {relativeTime(lastUpdated)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {live
            ? <span className="flex items-center gap-1.5 text-xs text-bull"><Wifi    className="h-3 w-3" /> Live</span>
            : <span className="flex items-center gap-1.5 text-xs text-caution"><WifiOff className="h-3 w-3" /> Demo</span>
          }
          <button onClick={fetchData} className="btn-ghost text-xs" disabled={loading}>
            <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          </button>
        </div>
      </div>

      {/* Ticker cards sorted by conviction */}
      {sorted.length > 0 ? (
        <div className="space-y-3">
          {sorted.map(rec => (
            <TickerBriefCard key={rec.id} rec={rec} />
          ))}
        </div>
      ) : (
        <div className="rounded-xl border border-bg-border bg-bg-card px-6 py-12 text-center">
          <p className="text-sm text-muted">No trade recommendations right now.</p>
          <p className="text-xs text-muted mt-1">The bot will post here when it sees a setup worth taking.</p>
        </div>
      )}
    </div>
  )
}
