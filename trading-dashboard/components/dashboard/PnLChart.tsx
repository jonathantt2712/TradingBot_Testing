'use client'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine, BarChart, Bar,
} from 'recharts'
import { useState } from 'react'
import type { PnLPoint } from '@/types/trading'
import { formatCurrency } from '@/lib/utils'

interface Props { data: PnLPoint[] }

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null
  const cum   = payload.find((p: any) => p.dataKey === 'cumulative_pnl')
  const daily = payload.find((p: any) => p.dataKey === 'daily_pnl')
  return (
    <div className="rounded-lg border border-bg-border bg-bg-elevated px-3 py-2 shadow-lg text-xs">
      <p className="text-muted mb-1">{label}</p>
      {cum   && <p className="text-brand-cyan font-medium">Cum: {formatCurrency(cum.value)}</p>}
      {daily && (
        <p className={daily.value >= 0 ? 'text-bull' : 'text-bear'}>
          Daily: {formatCurrency(daily.value)}
        </p>
      )}
    </div>
  )
}

export function PnLChart({ data }: Props) {
  const [view, setView] = useState<'cumulative' | 'daily'>('cumulative')
  const isPositive = (data.at(-1)?.cumulative_pnl ?? 0) >= 0

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h2 className="text-sm font-semibold text-primary">Equity Curve</h2>
          <p className="text-xs text-muted mt-0.5">30-day rolling P&L</p>
        </div>
        <div className="flex items-center gap-1 rounded-lg border border-bg-border p-0.5">
          {(['cumulative', 'daily'] as const).map(v => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`rounded-md px-3 py-1 text-xs font-medium transition-all ${
                view === v
                  ? 'bg-brand-cyan/10 text-brand-cyan'
                  : 'text-muted hover:text-subtle'
              }`}
            >
              {v === 'cumulative' ? 'Cumulative' : 'Daily'}
            </button>
          ))}
        </div>
      </div>

      <div className="h-[240px]">
        <ResponsiveContainer width="100%" height="100%">
          {view === 'cumulative' ? (
            <AreaChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="cumulativeGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%"   stopColor={isPositive ? '#22C55E' : '#EF4444'} stopOpacity={0.25} />
                  <stop offset="100%" stopColor={isPositive ? '#22C55E' : '#EF4444'} stopOpacity={0.0}  />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: '#64748B' }}
                tickFormatter={d => d.slice(5)}
                axisLine={false} tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#64748B' }}
                tickFormatter={v => `$${(v / 1000).toFixed(1)}k`}
                axisLine={false} tickLine={false} width={48}
              />
              <Tooltip content={<CustomTooltip />} />
              <ReferenceLine y={0} stroke="#334155" strokeDasharray="4 4" />
              <Area
                type="monotone"
                dataKey="cumulative_pnl"
                stroke={isPositive ? '#22C55E' : '#EF4444'}
                strokeWidth={2}
                fill="url(#cumulativeGrad)"
                dot={false}
                activeDot={{ r: 4, fill: isPositive ? '#22C55E' : '#EF4444', strokeWidth: 0 }}
              />
            </AreaChart>
          ) : (
            <BarChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: '#64748B' }}
                tickFormatter={d => d.slice(5)}
                axisLine={false} tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#64748B' }}
                tickFormatter={v => `$${v}`}
                axisLine={false} tickLine={false} width={48}
              />
              <Tooltip content={<CustomTooltip />} />
              <ReferenceLine y={0} stroke="#334155" />
              <Bar
                dataKey="daily_pnl"
                radius={[2, 2, 0, 0]}
                fill="#22C55E"
                // color each bar individually
                label={false}
              />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  )
}
