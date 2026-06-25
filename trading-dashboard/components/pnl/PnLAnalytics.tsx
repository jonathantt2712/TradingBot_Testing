'use client'
import { useState } from 'react'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
  PieChart, Pie, Cell, Legend,
} from 'recharts'
import { Info, X } from 'lucide-react'
import { cn, formatCurrency, formatPct } from '@/lib/utils'
import type { PnLPoint, PortfolioStats, TradeRecord } from '@/types/trading'

interface Props {
  pnl:         PnLPoint[]
  stats:       PortfolioStats
  trades:      TradeRecord[]
  live:        boolean
  attribution?: Record<string, { wins: number; losses: number; total: number; win_rate: number; total_pnl: number }>
  monteCarlo?: {
    actual_win_rate: number
    ci_95_lo:        number
    ci_95_hi:        number
    pnl_p5:          number
    pnl_p50:         number
    pnl_p95:         number
    n_trades:        number
    skill_signal:    boolean
    error?:          string
  }
  regimePerf?: Record<string, { trades: number; wins: number; win_rate: number; total_pnl: number; avg_pnl: number }>
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

export function PnLAnalytics({ pnl, stats, trades, live, attribution, monteCarlo, regimePerf }: Props) {
  const [sharpeModal, setSharpeModal] = useState(false)
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
    { label: 'Total Return', value: formatCurrency(stats.total_pnl),    sub: 'All time',           color: stats.total_pnl  >= 0 ? 'text-bull' : 'text-bear' },
    { label: 'Today',        value: formatCurrency(stats.today_pnl),    sub: 'Current session',    color: stats.today_pnl  >= 0 ? 'text-bull' : 'text-bear' },
    { label: 'Win Rate',     value: `${stats.win_rate.toFixed(1)}%`,    sub: `${wins}W / ${losses}L`, color: 'text-brand-cyan' },
    { label: 'Max Drawdown', value: formatPct(stats.max_drawdown),      sub: 'Peak → trough',      color: 'text-bear'       },
    { label: 'Avg R/R',      value: `${stats.avg_rr.toFixed(2)}x`,      sub: 'Expected value',     color: 'text-brand-cyan' },
  ]

  const sharpeValue = stats.sharpe_ratio === 0 ? '—' : stats.sharpe_ratio.toFixed(2)

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

        {/* Sharpe — separate so the info button works independently */}
        <div className="card p-4">
          <div className="flex items-center gap-1">
            <p className="stat-label text-[10px]">Sharpe</p>
            <button
              type="button"
              onClick={() => setSharpeModal(true)}
              className="flex items-center justify-center rounded p-0.5 text-muted hover:text-brand-cyan transition-colors"
            >
              <Info className="h-3 w-3" />
            </button>
          </div>
          <p className="mt-1 text-xl font-bold font-mono text-caution">{sharpeValue}</p>
          <p className="mt-0.5 text-[10px] text-muted">Risk-adj return</p>
        </div>
      </div>

      {/* Sharpe Ratio explanation modal */}
      {sharpeModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
          onClick={() => setSharpeModal(false)}
        >
          <div
            className="relative w-full max-w-md rounded-2xl border border-bg-border bg-bg-card p-6 shadow-2xl"
            onClick={e => e.stopPropagation()}
          >
            <button
              onClick={() => setSharpeModal(false)}
              className="absolute right-4 top-4 text-muted hover:text-primary transition-colors"
            >
              <X className="h-4 w-4" />
            </button>

            <h2 className="text-base font-bold text-primary mb-4">What is the Sharpe Ratio?</h2>

            <div className="space-y-3 text-sm text-subtle leading-relaxed">
              <p>
                The <span className="text-primary font-semibold">Sharpe Ratio</span> measures how much return the bot earns <em>relative to the risk it takes</em>. It answers the question: "Is the profit worth the volatility?"
              </p>

              <div className="rounded-lg bg-bg-base px-4 py-3 font-mono text-xs text-center text-brand-cyan">
                Sharpe = (avg daily return ÷ std deviation) × √252
              </div>

              <p>
                A higher number is better. The <span className="text-caution font-semibold">×√252</span> scales the daily result to an annual figure (252 trading days per year).
              </p>

              <div className="space-y-1.5 text-xs">
                <div className="flex items-center gap-2">
                  <span className="w-20 font-mono text-bull font-semibold">Above 2.0</span>
                  <span className="text-muted">Excellent — strong returns with low volatility</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-20 font-mono text-brand-cyan font-semibold">1.0 – 2.0</span>
                  <span className="text-muted">Good — solid risk-adjusted performance</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-20 font-mono text-caution font-semibold">0.0 – 1.0</span>
                  <span className="text-muted">Acceptable — but room to improve</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-20 font-mono text-bear font-semibold">Below 0</span>
                  <span className="text-muted">The strategy is losing on average</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-20 font-mono text-muted font-semibold">—</span>
                  <span className="text-muted">Not enough data yet (need at least 3 trading days)</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}


      {/* Portfolio Value + Equity Curve */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Portfolio value (absolute account balance) */}
        {pnl.some(p => p.equity != null) && (
          <div className="card p-5">
            <h2 className="text-sm font-semibold text-primary mb-4">Portfolio Value</h2>
            <div className="h-[220px]">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={pnl} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="portfolioGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor="#A78BFA" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#A78BFA" stopOpacity={0.0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
                  <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#64748B' }} tickFormatter={d => d.slice(5)} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 10, fill: '#64748B' }} tickFormatter={v => `$${(v/1000).toFixed(1)}k`} axisLine={false} tickLine={false} width={52} domain={['auto', 'auto']} />
                  <Tooltip content={<ChartTooltip />} />
                  <Area type="monotone" dataKey="equity" name="Account Value" stroke="#A78BFA" strokeWidth={2} fill="url(#portfolioGrad)" dot={false} activeDot={{ r: 4, strokeWidth: 0 }} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}

        {/* Cumulative P&L */}
        <div className="card p-5">
          <h2 className="text-sm font-semibold text-primary mb-4">Cumulative P&L</h2>
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

      {/* Agent Attribution */}
      {attribution && Object.keys(attribution).length > 0 && (
        <div className="card p-5">
          <h2 className="text-sm font-semibold text-primary mb-4">Agent Attribution</h2>
          <div className="space-y-3">
            {Object.entries(attribution)
              .sort(([, a], [, b]) => b.total - a.total)
              .map(([role, s]) => (
                <div key={role} className="flex items-center gap-3">
                  <span className="w-24 text-xs text-muted capitalize">{role}</span>
                  <div className="flex-1 h-2 rounded-full bg-bg-base overflow-hidden">
                    <div
                      className="h-full rounded-full bg-bull"
                      style={{ width: s.total > 0 ? `${s.win_rate}%` : '0%' }}
                    />
                  </div>
                  <span className="w-12 text-right text-xs font-mono text-subtle">{s.win_rate.toFixed(0)}%</span>
                  <span className="w-16 text-right text-xs font-mono text-muted">{s.wins}W/{s.losses}L</span>
                  <span className={cn('w-16 text-right text-xs font-mono', s.total_pnl >= 0 ? 'text-bull' : 'text-bear')}>
                    ${s.total_pnl >= 0 ? '+' : ''}{s.total_pnl.toFixed(0)}
                  </span>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Monte Carlo Win-Rate Confidence */}
      {monteCarlo && !monteCarlo.error && (monteCarlo.n_trades ?? 0) >= 10 && (
        <div className="card p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-primary">Win Rate Confidence (Monte Carlo)</h2>
            <span className={cn(
              'rounded-full border px-2 py-0.5 text-[10px] font-semibold',
              monteCarlo.skill_signal
                ? 'border-bull/30 text-bull bg-bull/10'
                : 'border-caution/30 text-caution bg-caution/10',
            )}>
              {monteCarlo.skill_signal ? 'Skill Signal' : 'Within Noise'}
            </span>
          </div>
          <div className="space-y-4">
            <div>
              <div className="flex justify-between text-xs text-muted mb-1.5">
                <span>{monteCarlo.ci_95_lo.toFixed(1)}%</span>
                <span className="font-semibold text-primary">{monteCarlo.actual_win_rate.toFixed(1)}% actual</span>
                <span>{monteCarlo.ci_95_hi.toFixed(1)}%</span>
              </div>
              <div className="relative h-3 rounded-full bg-bg-base overflow-hidden">
                <div
                  className="absolute h-full bg-brand-cyan/20 rounded-full"
                  style={{ left: `${monteCarlo.ci_95_lo}%`, width: `${monteCarlo.ci_95_hi - monteCarlo.ci_95_lo}%` }}
                />
                <div
                  className="absolute top-0 h-full w-0.5 bg-brand-cyan"
                  style={{ left: `${monteCarlo.actual_win_rate}%` }}
                />
              </div>
              <p className="text-[10px] text-muted mt-1.5">
                95% CI from {monteCarlo.n_trades} trades · 10,000 simulations
              </p>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {([
                { label: 'P5 PnL',  value: monteCarlo.pnl_p5,  color: 'text-bear'   },
                { label: 'Median',  value: monteCarlo.pnl_p50, color: 'text-subtle' },
                { label: 'P95 PnL', value: monteCarlo.pnl_p95, color: 'text-bull'   },
              ] as const).map(({ label, value, color }) => (
                <div key={label} className="rounded-lg bg-bg-base px-3 py-2 text-center">
                  <p className="text-[10px] text-muted mb-0.5">{label}</p>
                  <p className={cn('font-mono text-sm font-semibold', color)}>
                    {value >= 0 ? '+' : ''}${value.toFixed(0)}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Performance by Market Regime */}
      {regimePerf && Object.keys(regimePerf).length > 0 && (
        <div className="card p-5">
          <h2 className="text-sm font-semibold text-primary mb-4">Performance by Market Regime</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted border-b border-bg-border">
                  <th className="text-left pb-2 pr-4 font-medium">Regime</th>
                  <th className="text-right pb-2 pr-4 font-medium">Trades</th>
                  <th className="text-right pb-2 pr-4 font-medium">Win Rate</th>
                  <th className="text-right pb-2 pr-4 font-medium">Total P&amp;L</th>
                  <th className="text-right pb-2 font-medium">Avg P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(regimePerf)
                  .sort(([, a], [, b]) => b.trades - a.trades)
                  .map(([regime, s]) => (
                    <tr key={regime} className="border-b border-bg-border/50 last:border-0">
                      <td className="py-2 pr-4">
                        <span className={cn(
                          'rounded-full border px-2 py-0.5 capitalize font-medium',
                          regime === 'risk_on'  ? 'border-bull/30   text-bull     bg-bull/10'   :
                          regime === 'risk_off' ? 'border-bear/30   text-bear     bg-bear/10'   :
                          regime === 'choppy'   ? 'border-caution/30 text-caution bg-caution/10' :
                                                  'border-bg-border text-muted',
                        )}>
                          {regime.replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-right font-mono text-subtle">{s.trades}</td>
                      <td className="py-2 pr-4 text-right font-mono">
                        <span className={s.win_rate >= 50 ? 'text-bull' : 'text-bear'}>
                          {s.win_rate.toFixed(1)}%
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-right font-mono">
                        <span className={s.total_pnl >= 0 ? 'text-bull' : 'text-bear'}>
                          {s.total_pnl >= 0 ? '+' : ''}${s.total_pnl.toFixed(0)}
                        </span>
                      </td>
                      <td className="py-2 text-right font-mono">
                        <span className={s.avg_pnl >= 0 ? 'text-bull' : 'text-bear'}>
                          {s.avg_pnl >= 0 ? '+' : ''}${s.avg_pnl.toFixed(0)}
                        </span>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
