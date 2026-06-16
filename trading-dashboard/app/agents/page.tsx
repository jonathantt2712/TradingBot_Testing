'use client'
import { useState, useEffect, useCallback } from 'react'
import { RefreshCw, Wifi, WifiOff, Clock } from 'lucide-react'
import { demoRegime, demoRecommendations, api } from '@/lib/api'
import { AGENT_ORDER } from '@/lib/agents'
import { RegimeReasoningCard } from '@/components/agents/RegimeReasoningCard'
import { AgentOverviewCard } from '@/components/agents/AgentOverviewCard'
import type { TradeRecommendation, RegimeInfo } from '@/types/trading'
import { cn } from '@/lib/utils'

const REFRESH_MS = 30_000

function relativeTime(iso: string | null): string {
  if (!iso) return 'never'
  const diffMs = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diffMs / 60_000)
  if (mins < 1) return 'just now'
  if (mins === 1) return '1 minute ago'
  if (mins < 60) return `${mins} minutes ago`
  const hours = Math.floor(mins / 60)
  return `${hours} hour${hours === 1 ? '' : 's'} ago`
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
      if (reg.status === 'fulfilled') setRegime(reg.value)
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

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 md:space-y-6 max-w-[1400px] mx-auto">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-lg md:text-xl font-bold text-primary">Agents</h1>
          <p className="text-xs text-muted mt-0.5 flex items-center gap-1">
            <Clock className="h-3 w-3" /> Agents updated {relativeTime(lastUpdated)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {live
            ? <span className="flex items-center gap-1.5 text-xs text-bull"><Wifi className="h-3 w-3" /> Live</span>
            : <span className="flex items-center gap-1.5 text-xs text-caution"><WifiOff className="h-3 w-3" /> Demo</span>
          }
          <button onClick={fetchData} className="btn-ghost text-xs" disabled={loading}>
            <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          </button>
        </div>
      </div>

      <RegimeReasoningCard regime={regime} />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {AGENT_ORDER.map(role => (
          <AgentOverviewCard key={role} role={role} recommendations={recommendations} />
        ))}
      </div>
    </div>
  )
}
