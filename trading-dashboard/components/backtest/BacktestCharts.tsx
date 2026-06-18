'use client'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { cn } from '@/lib/utils'

// ── Equity curve — cumulative P&L over the trade sequence ──────────────────────

export interface BacktestTrade {
  entry_time: string
  exit_time:  string
  pnl_usd:    number
  outcome:    string
  ticker:     string
  direction:  string
}

function CurveTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return (
    <div className="rounded-lg border border-bg-border bg-bg-elevated px-3 py-2 shadow-lg text-xs">
      <p className="text-muted mb-1">Trade #{p.i} · {p.date}</p>
      <p className="font-mono text-primary">{p.ticker} <span className="text-muted">{p.direction}</span></p>
      <p className={p.pnl >= 0 ? 'text-bull' : 'text-bear'}>
        Trade: {p.pnl >= 0 ? '+' : ''}${p.pnl.toFixed(2)} <span className="text-muted">({p.outcome})</span>
      </p>
      <p className="text-brand-cyan">Cumulative: {p.cum >= 0 ? '+' : ''}${p.cum.toFixed(2)}</p>
    </div>
  )
}

export function EquityCurve({ trades, title }: { trades: BacktestTrade[]; title: string }) {
  if (!trades?.length) return null

  const sorted = [...trades].sort(
    (a, b) => new Date(a.exit_time).getTime() - new Date(b.exit_time).getTime(),
  )
  let cum = 0
  let peak = 0
  let maxDD = 0
  const data = sorted.map((t, i) => {
    cum += t.pnl_usd
    peak = Math.max(peak, cum)
    maxDD = Math.min(maxDD, cum - peak)
    return {
      i: i + 1,
      cum: +cum.toFixed(2),
      pnl: t.pnl_usd,
      ticker: t.ticker,
      direction: t.direction,
      outcome: t.outcome,
      date: (t.exit_time || t.entry_time || '').slice(0, 10),
    }
  })
  const final      = data[data.length - 1].cum
  const isPositive = final >= 0
  const stroke     = isPositive ? '#22C55E' : '#EF4444'

  return (
    <div className="card p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-primary">{title}</h2>
        <div className="flex items-center gap-3 text-xs">
          <span className={cn('font-mono font-bold', isPositive ? 'text-bull' : 'text-bear')}>
            {isPositive ? '+' : ''}${final.toFixed(2)}
          </span>
          <span className="text-muted">{data.length} trades</span>
          <span className="text-bear" title="Max peak-to-trough drawdown">DD ${maxDD.toFixed(0)}</span>
        </div>
      </div>
      <div className="h-[240px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%"   stopColor={stroke} stopOpacity={0.25} />
                <stop offset="100%" stopColor={stroke} stopOpacity={0.0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
            <XAxis dataKey="i" tick={{ fontSize: 10, fill: '#64748B' }}
                   axisLine={false} tickLine={false}
                   label={{ value: 'trade #', position: 'insideBottom', offset: -2, fontSize: 9, fill: '#475569' }} />
            <YAxis tick={{ fontSize: 10, fill: '#64748B' }}
                   tickFormatter={v => `$${v}`} axisLine={false} tickLine={false} width={52} />
            <Tooltip content={<CurveTooltip />} />
            <ReferenceLine y={0} stroke="#334155" strokeDasharray="4 4" />
            <Area type="monotone" dataKey="cum" stroke={stroke} strokeWidth={2}
                  fill="url(#eqGrad)" dot={false}
                  activeDot={{ r: 4, fill: stroke, strokeWidth: 0 }} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

// ── Optimizer parameter heatmap ────────────────────────────────────────────────

interface GridRecord {
  params:     Record<string, number>
  oos?:       Record<string, number>
  in_sample?: Record<string, number>
  [key: string]: unknown
}

function metric(r: GridRecord): { pnl: number | null; trades: number; win: number } {
  const m: any = r.oos ?? r
  return {
    pnl:    typeof m.total_pnl === 'number' ? m.total_pnl : null,
    trades: typeof m.total_trades === 'number' ? m.total_trades : 0,
    win:    typeof m.win_rate === 'number' ? m.win_rate : 0,
  }
}

export function ParamHeatmap({
  grid, xKey, yKey, xLabel, yLabel, title, subtitle,
}: {
  grid:     GridRecord[]
  xKey:     string
  yKey:     string
  xLabel:   string
  yLabel:   string
  title:    string
  subtitle: string
}) {
  if (!grid?.length) return null

  const cells = grid.map(r => ({
    x: r.params[xKey],
    y: r.params[yKey],
    ...metric(r),
  }))
  const xs = [...new Set(cells.map(c => c.x))].sort((a, b) => a - b)
  const ys = [...new Set(cells.map(c => c.y))].sort((a, b) => b - a) // high value on top
  const vals = cells.map(c => c.pnl).filter((v): v is number => v != null)
  if (!vals.length) return null
  const max = Math.max(...vals, 0)
  const min = Math.min(...vals, 0)
  const bestVal = Math.max(...vals)

  const lookup = (x: number, y: number) => cells.find(c => c.x === x && c.y === y)

  function bg(v: number | null): React.CSSProperties {
    if (v == null) return { background: '#162032' }            // bg-hover (no data)
    if (v >= 0) {
      const t = max > 0 ? v / max : 0
      return { background: `rgba(34,197,94,${0.10 + 0.65 * t})` }   // bull
    }
    const t = min < 0 ? v / min : 0
    return { background: `rgba(239,68,68,${0.10 + 0.65 * t})` }     // bear
  }

  return (
    <div className="card p-5 space-y-3">
      <div>
        <h2 className="text-sm font-semibold text-primary">{title}</h2>
        <p className="text-[11px] text-muted mt-0.5">{subtitle}</p>
      </div>

      <div className="overflow-x-auto">
        <div className="inline-block">
          {/* column headers */}
          <div className="flex">
            <div className="w-14 shrink-0" />
            {xs.map(x => (
              <div key={x} className="w-16 text-center text-[10px] text-muted font-mono">{x}</div>
            ))}
          </div>
          {/* rows */}
          {ys.map(y => (
            <div key={y} className="flex items-stretch">
              <div className="w-14 shrink-0 flex items-center justify-end pr-2 text-[10px] text-muted font-mono">{y}</div>
              {xs.map(x => {
                const c = lookup(x, y)
                const v = c?.pnl ?? null
                const isBest = v != null && v === bestVal
                return (
                  <div
                    key={`${x}-${y}`}
                    style={bg(v)}
                    title={
                      c && v != null
                        ? `${yLabel}=${y}  ${xLabel}=${x}\nOOS PnL: $${v.toFixed(0)}\nTrades: ${c.trades}  Win: ${c.win.toFixed(0)}%`
                        : `${yLabel}=${y}  ${xLabel}=${x}\nno data (too few trades)`
                    }
                    className={cn(
                      'w-16 h-11 m-0.5 rounded flex flex-col items-center justify-center cursor-default transition-transform hover:scale-105',
                      isBest && 'ring-2 ring-brand-cyan',
                    )}
                  >
                    {v != null ? (
                      <>
                        <span className="text-[11px] font-mono font-bold text-primary">
                          {v >= 0 ? '+' : ''}{Math.round(v)}
                        </span>
                        <span className="text-[8px] text-subtle">{c!.trades}t</span>
                      </>
                    ) : (
                      <span className="text-[9px] text-muted">—</span>
                    )}
                  </div>
                )
              })}
            </div>
          ))}
          {/* axis label */}
          <div className="flex">
            <div className="w-14 shrink-0" />
            <div className="text-[10px] text-muted mt-1" style={{ width: xs.length * 68 }}>
              <span className="block text-center">{xLabel} →</span>
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3 text-[10px] text-muted">
        <span className="flex items-center gap-1">
          <span className="h-3 w-3 rounded" style={{ background: 'rgba(34,197,94,0.7)' }} /> profit
        </span>
        <span className="flex items-center gap-1">
          <span className="h-3 w-3 rounded" style={{ background: 'rgba(239,68,68,0.7)' }} /> loss
        </span>
        <span className="flex items-center gap-1">
          <span className="h-3 w-3 rounded ring-2 ring-brand-cyan" /> best (OOS)
        </span>
        <span className="flex items-center gap-1">
          <span className="h-3 w-3 rounded" style={{ background: '#162032' }} /> too few trades
        </span>
        <span className="ml-auto">↑ {yLabel}</span>
      </div>
    </div>
  )
}
