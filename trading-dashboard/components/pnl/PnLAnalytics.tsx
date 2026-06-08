'use client'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
  PieChart, Pie, Cell, Legend,
} from 'recharts'
import { cn, formatCurrency, formatPct } from '@/lib/utils'
import type { PnLPoint, PortfolioStats, TradeRecord } from '@/types/trading'

interface Props {
  pnl:    PnLPoint[]
  stats:  PortfolioStats
  trades: TradeRecord[]
  live:   boolean
}

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-bg-border bg-bg-elevated px-3 py-2 text-xs shadow-lg">
      <p className="text-muted mb-1">{label}</p>
      {payload.map((p: any) => (
        <p key={p.dataKey} style={{ color: p.color }} className="font-mono font-medium">
          {p.name}: {typeof p.value === 'number' ? formatCurrency(p.value) : p.value}
        </p>
      ))}
    </div>
  )
}

export function PnLAnalytics({ pnl, stats, trades, live }: Props) {
  const wins   = trades.filter(t => (t.pnl ?? 0) > 0).length
  const losses = trades.filter(t => (t.pnl ?? 0) < 0).length
  const pie    = [
    { name: 'Wins',   value: wins,   color: '#22C55E' },
    { name: 'Losses', value: losses, color: '#EF4444' },
  ]

  // Monthly aggregation
  const monthly: Record<string, { pnl: number; trades: number }> = {}
  trades.forEach(t => {
    const key = (t.opened_at ?? '').slice(0, 7)
    if (!key) return
    if (!monthly[key]) monthly[key] = { pnl: 0, trades: 0 }
    monthly[key].pnl    += t.pnl ?? 0
    monthly[key].trades += 1
  })
  const monthlyArr = Object.entries(monthly)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, v]) => ({ date, ...v }))

  const summaryCards = [
    { label: 'Total Return',  value: formatCurrency(stats.total_pnl),    sub: 'All time',          color: stats.total_pnl  >= 0 ? 'text-bull' : 'text-bear' },
    { label: 'Today',         value: formatCurrency(stats.today_pnl),    sub: 'Current session',   color: stats.today_pnl  >= 0 ? 'text-bull' : 'text-bear' },
    { label: 'Win Rate',      value: `${stats.win_rate.toFixed(1)}%`,    sub: `${wins}W / ${losses}L`, color: 'text-brand-cyan' },
    { label: 'Max Drawdown',  value: formatPct(stats.max_drawdown),      sub: 'Peak → trough',     color: 'text-bear'       },
    { label: 'Sharpe',        value: stats.sharpe_ratio.toFixed(2),      sub: 'Risk-adj return',   color: 'text-caution'    },
    { label: 'Avg R/R',       value: `${stats.avg_rr.toFixed(2)}x`,      sub: 'Expected value',    color: 'text-brand-cyan' },
  ]

  return (
    <div className="px-6 py-6 space-y-6 max-w-[1400px]">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-primary">P&amp;L Analytics</h1>
          <p className="text-xs text-muted mt-0.5">Comprehensive performance breakdown</p>
        </div>
        {live
          ? <span className="flex items-center gap-1.5 text-xs text-bull"><span className="h-1.5 w-1.5 rounded-full bg-bull animate-pulse-slow" />Live — Alpaca</span>
          : <span className="text-xs text-caution">Demo data</span>
        }
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        {summaryCards.map(c => (
          <div key={c.label} className="card p-4">
            <p className="stat-label text-[10px]">{c.label}</p>
            <p className={cn('mt-1 text-xl font-bold font-mono', c.color)}>{c.value}</p>
            <p className="mt-0.5 text-[10px] text-muted">{c.sub}</p>
          </div>
        ))}
      </div>

      {/* Equity curve */}
      <div className="card p-5">
        <h2 className="text-sm font-semibold text-primary mb-4">Equity Curve (30 days)</h2>
        <div className="h-[220px]">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={pnl} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#06B6D4" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#06B6D4" stopOpacity={0.0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#64748B' }} tickFormatter={d => d.slice(5)} axisLine={false} tickLine={false} />
              <YAxis tick={{ fontSize: 10, fill: '#64748B' }} tickFormatter={v => `$${(v/1000).toFixed(1)}k`} axisLine={false} tickLine={false} width={48} />
              <Tooltip content={<ChartTooltip />} />
              <ReferenceLine y={0} stroke="#334155" strokeDasharray="4 4" />
              <Area type="monotone" dataKey="cumulative_pnl" name="Cumulative P&L" stroke="#06B6D4" strokeWidth={2} fill="url(#equityGrad)" dot={false} activeDot={{ r: 4, strokeWidth: 0 }} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Daily + Win/Loss */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_260px]">
        <div className="card p-5">
          <h2 className="text-sm font-semibold text-primary mb-4">Daily P&amp;L</h2>
          <div className="h-[200px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={pnl} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#64748B' }} tickFormatter={d => d.slice(5)} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 10, fill: '#64748B' }} axisLine={false} tickLine={false} width={48} />
                <Tooltip content={<ChartTooltip />} />
                <ReferenceLine y={0} stroke="#334155" />
                <Bar dataKey="daily_pnl" name="Daily P&L" radius={[2, 2, 0, 0]}>
                  {pnl.map((entry, i) => (
                    <Cell key={i} fill={entry.daily_pnl >= 0 ? '#22C55E' : '#EF4444'} fillOpacity={0.8} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="card p-5">
          <h2 className="text-sm font-semibold text-primary mb-4">Win / Loss Split</h2>
          <div className="h-[200px]">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={pie} cx="50%" cy="50%" innerRadius={55} outerRadius={75} paddingAngle={3} dataKey="value" stroke="none">
                  {pie.map((entry, i) => <Cell key={i} fill={entry.color} fillOpacity={0.85} />)}
                </Pie>
                <Legend iconType="circle" iconSize={8}
                  formatter={(v, e: any) => (
                    <span className="text-xs text-subtle">{v}: <span className="font-mono font-semibold" style={{ color: e.color }}>{e.payload.value}</span></span>
                  )}
                />
                <Tooltip contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', borderRadius: 8, fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Monthly */}
      {monthlyArr.length > 0 && (
        <div className="card p-5">
          <h2 className="text-sm font-semibold text-primary mb-4">Monthly Summary</h2>
          <div className="h-[180px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={monthlyArr} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#64748B' }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 10, fill: '#64748B' }} axisLine={false} tickLine={false} width={48} />
                <Tooltip content={<ChartTooltip />} />
                <ReferenceLine y={0} stroke="#334155" />
                <Bar dataKey="pnl" name="Monthly P&L" radius={[3, 3, 0, 0]}>
                  {monthlyArr.map((e, i) => (
                    <Cell key={i} fill={e.pnl >= 0 ? '#06B6D4' : '#EF4444'} fillOpacity={0.85} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  )
}
