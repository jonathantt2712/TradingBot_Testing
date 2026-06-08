'use client'
import { useState, useEffect } from 'react'
import {
  TrendingUp, TrendingDown, BarChart2, Target, AlertTriangle,
  RefreshCw, CheckCircle2, XCircle, Clock, Activity,
} from 'lucide-react'
import { cn } from '@/lib/utils'

interface TickerStat {
  ticker:   string
  trades:   number
  pnl:      number
  win_rate: number
}

interface BacktestData {
  total_trades:   number
  wins?:          number
  losses?:        number
  eods?:          number
  win_rate:       number
  total_pnl:      number
  avg_win:        number
  avg_loss:       number
  profit_factor:  number
  sharpe:         number
  max_drawdown:   number
  ev_per_trade?:  number
  by_ticker:      TickerStat[]
  optimal_params?: Record<string, number>
  optimal_window_days?: number
}

interface BacktestPayload {
  results:    BacktestData | null
  optimal:    BacktestData | null
  configText: string | null
}

function StatCard({
  label, value, sub, color = 'text-primary', icon: Icon,
}: {
  label:  string
  value:  string
  sub?:   string
  color?: string
  icon:   React.ElementType
}) {
  return (
    <div className="card p-4 space-y-2">
      <div className="flex items-center gap-2">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-bg-hover">
          <Icon className={cn('h-3.5 w-3.5', color)} />
        </div>
        <p className="text-xs text-muted">{label}</p>
      </div>
      <p className={cn('text-xl font-bold font-mono', color)}>{value}</p>
      {sub && <p className="text-[10px] text-muted">{sub}</p>}
    </div>
  )
}

function DatasetPanel({ data, title, badge }: { data: BacktestData; title: string; badge?: string }) {
  const pnlPos = data.total_pnl >= 0

  return (
    <div className="card p-5 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-primary">{title}</h2>
        {badge && (
          <span className="rounded-full bg-brand-cyan/10 border border-brand-cyan/20 px-2.5 py-0.5 text-[10px] font-medium text-brand-cyan">
            {badge}
          </span>
        )}
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Total P&L"     value={`$${data.total_pnl >= 0 ? '+' : ''}${data.total_pnl.toFixed(2)}`}
                  color={pnlPos ? 'text-bull' : 'text-bear'} icon={pnlPos ? TrendingUp : TrendingDown} />
        <StatCard label="Win Rate"      value={`${data.win_rate.toFixed(1)}%`}
                  color={data.win_rate >= 45 ? 'text-bull' : data.win_rate >= 35 ? 'text-caution' : 'text-bear'}
                  icon={Target} sub={`${data.wins ?? '—'} wins / ${data.losses ?? '—'} SL / ${data.eods ?? '—'} EOD`} />
        <StatCard label="Profit Factor" value={data.profit_factor.toFixed(2)}
                  color={data.profit_factor >= 1.5 ? 'text-bull' : data.profit_factor >= 1 ? 'text-caution' : 'text-bear'}
                  icon={BarChart2} />
        <StatCard label="Sharpe Ratio"  value={data.sharpe.toFixed(2)}
                  color={data.sharpe >= 1.5 ? 'text-bull' : data.sharpe >= 0 ? 'text-caution' : 'text-bear'}
                  icon={Activity} />
        <StatCard label="Total Trades"  value={String(data.total_trades)} icon={CheckCircle2} />
        <StatCard label="Avg Win"       value={`$${data.avg_win.toFixed(2)}`}   color="text-bull"  icon={TrendingUp} />
        <StatCard label="Avg Loss"      value={`$${data.avg_loss.toFixed(2)}`}  color="text-bear"  icon={TrendingDown} />
        <StatCard label="Max Drawdown"  value={`$${data.max_drawdown.toFixed(2)}`} color="text-bear" icon={AlertTriangle}
                  sub={data.ev_per_trade != null ? `EV/trade: $${data.ev_per_trade >= 0 ? '+' : ''}${data.ev_per_trade.toFixed(2)}` : undefined} />
      </div>

      {/* Ticker breakdown */}
      {data.by_ticker?.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted mb-2">By Ticker</p>
          <div className="rounded-lg border border-bg-border overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-bg-border bg-bg-base">
                  <th className="px-3 py-2 text-left text-muted font-medium">Ticker</th>
                  <th className="px-3 py-2 text-right text-muted font-medium">Trades</th>
                  <th className="px-3 py-2 text-right text-muted font-medium">Win %</th>
                  <th className="px-3 py-2 text-right text-muted font-medium">P&L</th>
                  <th className="px-3 py-2 text-right text-muted font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {data.by_ticker.map((tk, i) => {
                  const pos = tk.pnl >= 0
                  const barW = Math.min(Math.abs(tk.pnl) / (Math.max(...data.by_ticker.map(t => Math.abs(t.pnl))) || 1) * 100, 100)
                  return (
                    <tr key={tk.ticker} className={cn('border-b border-bg-border last:border-0', i % 2 === 0 ? '' : 'bg-bg-base/50')}>
                      <td className="px-3 py-2 font-mono font-semibold text-primary">{tk.ticker}</td>
                      <td className="px-3 py-2 text-right text-muted">{tk.trades}</td>
                      <td className={cn('px-3 py-2 text-right font-mono', tk.win_rate >= 50 ? 'text-bull' : 'text-caution')}>
                        {tk.win_rate.toFixed(1)}%
                      </td>
                      <td className={cn('px-3 py-2 text-right font-mono font-semibold', pos ? 'text-bull' : 'text-bear')}>
                        {pos ? '+' : ''}{tk.pnl.toFixed(2)}
                      </td>
                      <td className="px-3 py-2 w-20">
                        <div className="h-1.5 rounded-full bg-bg-hover overflow-hidden">
                          <div
                            className={cn('h-full rounded-full transition-all', pos ? 'bg-bull/60' : 'bg-bear/60')}
                            style={{ width: `${barW}%` }}
                          />
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Optimal params if present */}
      {data.optimal_params && (
        <div>
          <p className="text-xs font-medium text-muted mb-2">Optimal Parameters</p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {Object.entries(data.optimal_params).map(([k, v]) => (
              <div key={k} className="rounded-lg bg-bg-base border border-bg-border px-3 py-2">
                <p className="text-[10px] text-muted">{k.replace(/_/g, ' ')}</p>
                <p className="text-sm font-mono font-bold text-brand-cyan">{v}</p>
              </div>
            ))}
          </div>
          {data.optimal_window_days && (
            <p className="text-[10px] text-muted mt-1">
              Window: {data.optimal_window_days} days
            </p>
          )}
        </div>
      )}
    </div>
  )
}

export default function BacktestPage() {
  const [data,    setData]    = useState<BacktestPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  async function load() {
    setLoading(true); setError(null)
    try {
      const res = await fetch('/api/backtest', { cache: 'no-store' })
      if (!res.ok) throw new Error(`API ${res.status}`)
      setData(await res.json())
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 md:space-y-6 max-w-[1400px]">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-primary">Backtest Results</h1>
          <p className="text-xs text-muted mt-0.5">Walk-forward day-trade simulation · research-filtered signals</p>
        </div>
        <button onClick={load} disabled={loading} className="btn-ghost text-xs">
          <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          Refresh
        </button>
      </div>

      {loading && (
        <div className="card flex items-center justify-center py-20">
          <RefreshCw className="h-6 w-6 text-brand-cyan animate-spin" />
        </div>
      )}

      {error && (
        <div className="card flex items-center gap-3 p-5 border-bear/30">
          <XCircle className="h-5 w-5 text-bear shrink-0" />
          <div>
            <p className="text-sm font-medium text-bear">Failed to load backtest data</p>
            <p className="text-xs text-muted mt-0.5">{error} — Run the optimizer to generate results.</p>
          </div>
        </div>
      )}

      {!loading && data && (
        <div className="space-y-6">
          {/* Research filters notice */}
          <div className="rounded-xl border border-brand-cyan/20 bg-brand-cyan/5 px-4 py-3 flex items-start gap-3">
            <Activity className="h-4 w-4 text-brand-cyan shrink-0 mt-0.5" />
            <div>
              <p className="text-xs font-semibold text-brand-cyan">Research Filters Active</p>
              <p className="text-[11px] text-muted mt-0.5">
                PEAD open-noise filter (skip 9:30–10:00 ET) · Lottery/CPT dynamic SL ·
                Volume confirmation (1.3× 20-day avg) · Retail attention classifier (+5 threshold)
              </p>
            </div>
          </div>

          {/* Comparison layout */}
          {data.optimal && data.results && (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <DatasetPanel data={data.results} title="Latest Backtest (30-day)" />
              <DatasetPanel data={data.optimal} title="Optimizer Run (best window)" badge="Optimal Params" />
            </div>
          )}

          {/* If only one exists */}
          {data.optimal && !data.results && (
            <DatasetPanel data={data.optimal} title="Optimizer Run" badge="Optimal Params" />
          )}
          {data.results && !data.optimal && (
            <DatasetPanel data={data.results} title="Latest Backtest (30-day)" />
          )}

          {/* No data */}
          {!data.optimal && !data.results && (
            <div className="card flex flex-col items-center justify-center py-20 gap-3">
              <Clock className="h-8 w-8 text-muted" />
              <p className="text-sm text-muted">No backtest data yet.</p>
              <p className="text-xs text-muted/60">Run <code className="text-brand-cyan">run_optimizer.bat</code> or <code className="text-brand-cyan">backtest_30day.py</code> to generate results.</p>
            </div>
          )}

          {/* Raw config text */}
          {data.configText && (
            <div className="card p-5 space-y-2">
              <p className="text-xs font-medium text-muted">OPTIMAL_CONFIG.txt</p>
              <pre className="text-[11px] font-mono text-subtle bg-bg-base rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">
                {data.configText}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
