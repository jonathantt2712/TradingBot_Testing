'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  LineChart, Line, BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { RefreshCw, Wifi, WifiOff, Sparkles } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { LearningData } from '@/types/trading'

const REFRESH_MS = 30_000

// Stable colour per agent so a line keeps its colour across every chart.
const AGENT_COLORS: Record<string, string> = {
  technical:   '#22D3EE',
  fundamental: '#60A5FA',
  vision:      '#A78BFA',
  liquid:      '#FBBF24',
  insider:     '#F87171',
  squeeze:     '#FB923C',
  macro:       '#34D399',
}
const colorFor = (a: string) => AGENT_COLORS[a] ?? '#94A3B8'

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })

const axisTick = { fontSize: 10, fill: '#64748B' }

function StatCard({ label, value, tone }: { label: string; value: string; tone?: 'bull' | 'bear' | 'cyan' }) {
  const color = tone === 'bull' ? 'text-bull' : tone === 'bear' ? 'text-bear' : tone === 'cyan' ? 'text-brand-cyan' : 'text-primary'
  return (
    <div className="card p-4">
      <div className={cn('text-2xl font-bold', color)}>{value}</div>
      <div className="text-xs text-muted mt-1">{label}</div>
    </div>
  )
}

export default function LearningPage() {
  const [data,    setData]    = useState<LearningData | null>(null)
  const [live,    setLive]    = useState(false)
  const [loading, setLoading] = useState(true)
  const [simulating, setSimulating] = useState(false)

  const runSimulation = useCallback(async () => {
    setSimulating(true)
    try {
      const d = await api.simulateLearning()
      setData(d)
      setLive(true)
    } catch {
      /* leave the empty state in place */
    } finally {
      setSimulating(false)
    }
  }, [])

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api.learning()
      setData(d)
      setLive(true)
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

  const agents = useMemo(() => {
    const set = new Set<string>()
    data?.history.forEach(s => Object.keys(s.weights || {}).forEach(a => set.add(a)))
    Object.keys(data?.weights || {}).forEach(a => set.add(a))
    return Array.from(set)
  }, [data])

  // Flatten history into recharts rows.
  const weightRows = useMemo(() =>
    (data?.history ?? []).map(s => ({
      ts: fmtTime(s.ts),
      ...Object.fromEntries(agents.map(a => [a, +(((s.weights?.[a] ?? 0) * 100).toFixed(2))])),
    })), [data, agents])

  const perfRows = useMemo(() =>
    (data?.history ?? []).map(s => ({
      ts: fmtTime(s.ts),
      win_rate: s.win_rate, long: s.long_win_rate, short: s.short_win_rate,
      long_thr: s.long_threshold, short_thr: s.short_threshold,
    })), [data])

  const multRows = useMemo(() =>
    agents
      .map(a => ({ agent: a, mult: +(data?.multipliers?.[a] ?? 1).toFixed(2) }))
      .sort((x, y) => y.mult - x.mult),
    [data, agents])

  const hasHistory = (data?.history?.length ?? 0) > 0

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 max-w-[1100px] mx-auto">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-lg font-bold text-primary flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-brand-cyan" /> Learning
          </h1>
          {data?.active
            ? <span className="rounded-full border border-bull/30 bg-bull/10 px-3 py-0.5 text-xs font-bold text-bull">ADAPTING LIVE</span>
            : <span className="rounded-full border border-bg-border px-3 py-0.5 text-xs font-bold text-muted">WARMING UP</span>}
          {data?.simulated && <span className="rounded-full border border-caution/40 bg-caution/10 px-3 py-0.5 text-xs font-bold text-caution">SIMULATED</span>}
          {data && <span className="text-xs text-muted">{data.steps} tuning steps · {data.sample_size} trades in window</span>}
        </div>
        <div className="flex items-center gap-2">
          {live
            ? <span className="flex items-center gap-1.5 text-xs text-bull"><Wifi className="h-3 w-3" /> Live</span>
            : <span className="flex items-center gap-1.5 text-xs text-caution"><WifiOff className="h-3 w-3" /> Offline</span>}
          <button onClick={fetchData} className="btn-ghost text-xs" disabled={loading}>
            <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          </button>
        </div>
      </div>

      <p className="text-xs text-muted max-w-2xl">
        After every closed trade the bot scores each agent against the actual outcome and re-weights
        them — shifting trust toward the agents that have been right. This is that adaptation, live.
      </p>

      {/* Summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Win Rate (window)" value={data?.win_rate != null ? `${data.win_rate.toFixed(1)}%` : '—'}
          tone={(data?.win_rate ?? 0) >= 50 ? 'bull' : 'bear'} />
        <StatCard label="Learned Bias" value={(data?.bias ?? 'neutral').toUpperCase()} tone="cyan" />
        <StatCard label="LONG / SHORT Threshold"
          value={data?.long_threshold != null ? `${data.long_threshold} / ${data.short_threshold}` : '—'} />
        <StatCard label="Tuning Steps" value={String(data?.steps ?? 0)} />
      </div>

      {!hasHistory && (
        <div className="card p-8 text-center">
          <Sparkles className="h-8 w-8 text-brand-cyan/40 mx-auto mb-3" />
          <p className="text-sm text-primary font-medium">No learning steps yet</p>
          <p className="text-xs text-muted mt-1.5 max-w-md mx-auto">
            The tuner starts adapting after ~10 trades have closed. As trades resolve, agent weights
            begin to drift here automatically — nothing to run, just check back.
          </p>
          <button
            onClick={runSimulation}
            disabled={simulating}
            className="btn-ghost text-xs mt-5 mx-auto inline-flex items-center gap-1.5"
          >
            <Sparkles className={cn('h-3.5 w-3.5', simulating && 'animate-pulse')} />
            {simulating ? 'Simulating…' : 'Preview with simulated data'}
          </button>
          <p className="text-[11px] text-muted/70 mt-2">
            Runs the real tuner over a synthetic track record so you can see the charts now.
            Clearly badged SIMULATED — replaced the moment real scored trades arrive.
          </p>
        </div>
      )}

      {hasHistory && (
        <>
          {/* Agent weights over time */}
          <div className="card p-5">
            <h2 className="text-sm font-semibold text-primary">Agent Weights Over Time</h2>
            <p className="text-xs text-muted mt-0.5 mb-4">Share of the composite score each agent commands (%) — trust shifting to the agents that are right</p>
            <div className="h-[300px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={weightRows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
                  <XAxis dataKey="ts" tick={axisTick} axisLine={false} tickLine={false} minTickGap={32} />
                  <YAxis tick={axisTick} axisLine={false} tickLine={false} width={40} tickFormatter={v => `${v}%`} />
                  <Tooltip contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', borderRadius: 8, fontSize: 12 }} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {agents.map(a => (
                    <Line key={a} type="monotone" dataKey={a} stroke={colorFor(a)} strokeWidth={2} dot={false} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Win rate over time */}
            <div className="card p-5">
              <h2 className="text-sm font-semibold text-primary">Win Rate Over Time</h2>
              <p className="text-xs text-muted mt-0.5 mb-4">Overall / long / short, rolling window</p>
              <div className="h-[260px]">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={perfRows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
                    <XAxis dataKey="ts" tick={axisTick} axisLine={false} tickLine={false} minTickGap={32} />
                    <YAxis tick={axisTick} axisLine={false} tickLine={false} width={40} domain={[0, 100]} tickFormatter={v => `${v}%`} />
                    <Tooltip contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', borderRadius: 8, fontSize: 12 }} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <ReferenceLine y={50} stroke="#334155" strokeDasharray="4 4" />
                    <Line type="monotone" dataKey="win_rate" name="Overall" stroke="#22D3EE" strokeWidth={2} dot={false} />
                    <Line type="monotone" dataKey="long"  name="Long"  stroke="#22C55E" strokeWidth={1.5} dot={false} />
                    <Line type="monotone" dataKey="short" name="Short" stroke="#EF4444" strokeWidth={1.5} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Thresholds over time */}
            <div className="card p-5">
              <h2 className="text-sm font-semibold text-primary">Entry Thresholds</h2>
              <p className="text-xs text-muted mt-0.5 mb-4">Self-adjusting conviction bars — tighten when losing, loosen when winning</p>
              <div className="h-[260px]">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={perfRows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
                    <XAxis dataKey="ts" tick={axisTick} axisLine={false} tickLine={false} minTickGap={32} />
                    <YAxis tick={axisTick} axisLine={false} tickLine={false} width={40} domain={[20, 80]} />
                    <Tooltip contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', borderRadius: 8, fontSize: 12 }} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Line type="stepAfter" dataKey="long_thr"  name="LONG above"  stroke="#22C55E" strokeWidth={2} dot={false} />
                    <Line type="stepAfter" dataKey="short_thr" name="SHORT below" stroke="#EF4444" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {/* Current multipliers */}
          <div className="card p-5">
            <h2 className="text-sm font-semibold text-primary">Current Agent Skill Multipliers</h2>
            <p className="text-xs text-muted mt-0.5 mb-4">2× = always right · 1× = coin-flip · 0.1× = always wrong</p>
            <div className="h-[260px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={multRows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" vertical={false} />
                  <XAxis dataKey="agent" tick={axisTick} axisLine={false} tickLine={false} />
                  <YAxis tick={axisTick} axisLine={false} tickLine={false} width={40} domain={[0, 2]} />
                  <Tooltip contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', borderRadius: 8, fontSize: 12 }} />
                  <ReferenceLine y={1} stroke="#475569" strokeDasharray="4 4" />
                  <Bar dataKey="mult" radius={[3, 3, 0, 0]}>
                    {multRows.map(r => (
                      <Cell key={r.agent} fill={r.mult >= 1 ? colorFor(r.agent) : '#64748B'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
