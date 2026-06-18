'use client'
import { useState, useEffect, useRef } from 'react'
import {
  TrendingUp, TrendingDown, BarChart2, Target, AlertTriangle,
  RefreshCw, CheckCircle2, XCircle, Clock, Activity, Play, Loader2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { EquityCurve, ParamHeatmap, type BacktestTrade } from '@/components/backtest/BacktestCharts'
import { ChallengePanel } from '@/components/backtest/ChallengePanel'

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
  trades?:        BacktestTrade[]
  optimal_params?: Record<string, number>
  optimal_window_days?: number
}

interface GridRecord {
  params: Record<string, number>
  oos?:        Record<string, number>
  in_sample?:  Record<string, number>
  [key: string]: unknown
}

interface OptimizerData {
  run_at?:         string
  days?:           number
  objective?:      string
  validation?:     string
  threshold_grid?: GridRecord[]
  atr_grid?:       GridRecord[]
  best?:           GridRecord
}

interface BacktestPayload {
  results:    BacktestData | null
  optimal:    BacktestData | null
  optimizer:  OptimizerData | null
  configText: string | null
}

interface BacktestHealth {
  last_run_at: string | null
  last_status: 'ok' | 'failed' | 'timeout' | null
  error_count: number
  last_error:  string | null
}

interface RejectionRecord {
  ts:              string
  ticker:          string
  reason:          string
  composite_score: number
  [key: string]:   unknown
}

function relativeTime(iso: string): string {
  const ms   = Date.now() - new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).getTime()
  const mins = Math.floor(ms / 60000)
  if (mins < 1)  return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24)  return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
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
  const [data,       setData]       = useState<BacktestPayload | null>(null)
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState<string | null>(null)
  const [running,    setRunning]    = useState(false)
  const [optimizing, setOptimizing] = useState(false)
  const [applying,   setApplying]   = useState(false)
  const [applyMsg,   setApplyMsg]   = useState<{ ok: boolean; text: string } | null>(null)
  const [health,     setHealth]     = useState<BacktestHealth | null>(null)
  const [optHealth,  setOptHealth]  = useState<BacktestHealth | null>(null)
  const [rejections, setRejections] = useState<RejectionRecord[]>([])
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const optPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  async function loadHealth() {
    try {
      const res = await fetch('/api/bot/health', { cache: 'no-store' })
      if (res.ok) {
        const d = await res.json()
        setHealth(d.backtest ?? null)
        setOptHealth(d.optimizer ?? null)
      }
    } catch { /* bot offline */ }
  }

  async function loadRejections() {
    try {
      const res = await fetch('/api/bot/rejections')
      if (res.ok) {
        const data = await res.json()
        if (Array.isArray(data)) setRejections(data.slice(-10).reverse())
      }
    } catch { /* ignore */ }
  }

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
    loadHealth()
    loadRejections()
  }

  async function runBacktest() {
    if (running) return
    setRunning(true)
    try {
      await fetch('/api/backtest/run', { method: 'POST' })
    } catch { /* ignore trigger errors — bot may queue it */ }
    // Poll for updated results every 10s while running
    pollRef.current = setInterval(async () => {
      const res = await fetch('/api/backtest', { cache: 'no-store' })
      if (res.ok) {
        const fresh = await res.json()
        setData(fresh)
        loadHealth()
        if (fresh.results || fresh.optimal) {
          if (pollRef.current) clearInterval(pollRef.current)
          setRunning(false)
        }
      }
    }, 10_000)
  }

  async function runOptimizer() {
    if (optimizing) return
    setOptimizing(true)
    try {
      await fetch('/api/optimize/run', { method: 'POST' })
    } catch { /* ignore trigger errors — bot may queue it */ }
    // The optimizer is heavy (grid × walk-forward). Poll health for completion,
    // then reload results (it writes backtest_optimal.json which /api/backtest serves).
    optPollRef.current = setInterval(async () => {
      try {
        const res = await fetch('/api/bot/health', { cache: 'no-store' })
        if (res.ok) {
          const d = await res.json()
          setOptHealth(d.optimizer ?? null)
          if (d.optimizer && d.optimizer.running === false) {
            if (optPollRef.current) clearInterval(optPollRef.current)
            setOptimizing(false)
            load()
          }
        }
      } catch { /* keep polling */ }
    }, 15_000)
  }

  async function applyOptimal() {
    if (applying) return
    setApplying(true); setApplyMsg(null)
    try {
      const res = await fetch('/api/optimize/apply', { method: 'POST' })
      const d = await res.json()
      if (d.status === 'applied') {
        const parts = Object.entries(d.applied || {}).map(([k, v]) => `${k}=${v}`).join(', ')
        setApplyMsg({ ok: true, text: `Applied to live trading (OOS $${d.oos_pnl}): ${parts}` })
      } else if (d.status === 'rejected') {
        setApplyMsg({ ok: false, text: `Rejected — ${d.reason}` })
      } else {
        setApplyMsg({ ok: false, text: d.reason || 'Could not apply params' })
      }
    } catch {
      setApplyMsg({ ok: false, text: 'Failed to reach the bot' })
    } finally {
      setApplying(false)
    }
  }

  useEffect(() => { load() }, [])
  useEffect(() => () => {
    if (pollRef.current) clearInterval(pollRef.current)
    if (optPollRef.current) clearInterval(optPollRef.current)
  }, [])

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 md:space-y-6 max-w-[1400px]">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-primary">Backtest Results</h1>
          <p className="text-xs text-muted mt-0.5">Walk-forward day-trade simulation · research-filtered signals</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={runBacktest}
            disabled={running}
            className={cn(
              'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all',
              'bg-brand-cyan/15 border border-brand-cyan/30 text-brand-cyan hover:bg-brand-cyan/25',
              'disabled:opacity-50 disabled:cursor-not-allowed',
            )}
          >
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            {running ? 'Running...' : 'Run Backtest'}
          </button>
          <button
            onClick={runOptimizer}
            disabled={optimizing}
            title="Walk-forward profit optimizer — tunes thresholds & ATR on held-out data"
            className={cn(
              'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all',
              'bg-bull/15 border border-bull/30 text-bull hover:bg-bull/25',
              'disabled:opacity-50 disabled:cursor-not-allowed',
            )}
          >
            {optimizing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Target className="h-3.5 w-3.5" />}
            {optimizing ? 'Optimizing...' : 'Run Optimizer'}
          </button>
          {data?.optimizer?.best && (
            <button
              onClick={applyOptimal}
              disabled={applying}
              title="Apply the optimizer's best params to LIVE trading (no redeploy). Guarded against unprofitable params."
              className={cn(
                'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all',
                'bg-caution/15 border border-caution/30 text-caution hover:bg-caution/25',
                'disabled:opacity-50 disabled:cursor-not-allowed',
              )}
            >
              {applying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
              {applying ? 'Applying...' : 'Apply to Live'}
            </button>
          )}
          <button onClick={load} disabled={loading} className="btn-ghost text-xs">
            <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
            Refresh
          </button>
        </div>
      </div>

      {/* Auto-backtest status — shows the 24/7 scheduler is running */}
      <div className="rounded-xl border border-bg-border bg-bg-card px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs">
        <span className="flex items-center gap-1.5 font-medium text-primary">
          <span className={cn(
            'h-2 w-2 rounded-full',
            running ? 'bg-brand-cyan animate-pulse'
              : health?.last_status === 'ok' ? 'bg-bull'
              : health?.last_status ? 'bg-bear' : 'bg-muted',
          )} />
          Auto-backtest
        </span>
        <span className="text-muted">Runs automatically every day after market close (server runs 24/7).</span>
        {running ? (
          <span className="text-brand-cyan font-medium">Running now…</span>
        ) : health?.last_run_at ? (
          <span className="text-subtle">
            Last run: {relativeTime(health.last_run_at)}
            {health.last_status === 'ok'
              ? <span className="text-bull ml-1">· ok</span>
              : <span className="text-bear ml-1">· {health.last_status}</span>}
          </span>
        ) : (
          <span className="text-muted">No run recorded yet — first run happens on next server start or close.</span>
        )}
        {(health?.error_count ?? 0) > 0 && (
          <span className="text-bear font-semibold">Failures: {health!.error_count}</span>
        )}
      </div>

      {/* Optimizer status */}
      <div className="rounded-xl border border-bg-border bg-bg-card px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs">
        <span className="flex items-center gap-1.5 font-medium text-primary">
          <span className={cn(
            'h-2 w-2 rounded-full',
            optimizing ? 'bg-bull animate-pulse'
              : optHealth?.last_status === 'ok' ? 'bg-bull'
              : optHealth?.last_status ? 'bg-bear' : 'bg-muted',
          )} />
          Profit Optimizer
        </span>
        <span className="text-muted">Walk-forward tune of thresholds &amp; ATR, ranked on held-out (out-of-sample) profit.</span>
        {optimizing ? (
          <span className="text-bull font-medium">Running now… (this takes a few minutes)</span>
        ) : optHealth?.last_run_at ? (
          <span className="text-subtle">
            Last run: {relativeTime(optHealth.last_run_at)}
            {optHealth.last_status === 'ok'
              ? <span className="text-bull ml-1">· ok</span>
              : <span className="text-bear ml-1">· {optHealth.last_status}</span>}
          </span>
        ) : (
          <span className="text-muted">Not run yet — click “Run Optimizer” to tune for maximum profit.</span>
        )}
        {(optHealth?.error_count ?? 0) > 0 && (
          <span className="text-bear font-semibold">Failures: {optHealth!.error_count}</span>
        )}
      </div>

      {/* Apply-to-live result */}
      {applyMsg && (
        <div className={cn(
          'rounded-xl border px-4 py-3 flex items-start gap-2.5 text-xs',
          applyMsg.ok ? 'border-bull/30 bg-bull/5' : 'border-bear/30 bg-bear/5',
        )}>
          {applyMsg.ok
            ? <CheckCircle2 className="h-4 w-4 text-bull shrink-0 mt-0.5" />
            : <XCircle className="h-4 w-4 text-bear shrink-0 mt-0.5" />}
          <p className={applyMsg.ok ? 'text-bull' : 'text-bear'}>{applyMsg.text}</p>
        </div>
      )}

      {rejections.length > 0 && (
        <div className="rounded-xl border border-bg-border bg-bg-card px-4 py-3">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted mb-2">
            Recent Trade Rejections
          </p>
          <div className="space-y-1">
            {rejections.map((r, i) => (
              <div key={i} className="flex items-center justify-between text-xs">
                <span className="font-mono text-primary">{r.ticker}</span>
                <span className="text-muted capitalize">{r.reason.replace(/_/g, ' ')}</span>
                <span className="text-subtle">{new Date(r.ts).toLocaleTimeString()}</span>
              </div>
            ))}
          </div>
        </div>
      )}

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

          {/* Equity curve — cumulative P&L over the backtest trade sequence */}
          {data.results?.trades && data.results.trades.length > 0 && (
            <EquityCurve trades={data.results.trades} title="Equity Curve — Latest Backtest" />
          )}

          {/* Optimizer parameter heatmaps */}
          {data.optimizer && (data.optimizer.threshold_grid?.length || data.optimizer.atr_grid?.length) ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <h2 className="text-sm font-bold text-primary">Optimizer Grid</h2>
                {data.optimizer.objective && (
                  <span className="text-[11px] text-muted">
                    objective: <span className="text-bull font-medium">{data.optimizer.objective}</span>
                  </span>
                )}
                {data.optimizer.validation && (
                  <span className="text-[11px] text-muted">· {data.optimizer.validation}</span>
                )}
                {data.optimizer.days && (
                  <span className="text-[11px] text-muted">· {data.optimizer.days}d window</span>
                )}
              </div>
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                {data.optimizer.threshold_grid && data.optimizer.threshold_grid.length > 0 && (
                  <ParamHeatmap
                    grid={data.optimizer.threshold_grid}
                    xKey="SHORT_THRESHOLD" yKey="LONG_THRESHOLD"
                    xLabel="SHORT" yLabel="LONG"
                    title="Entry Thresholds"
                    subtitle="Held-out (OOS) profit per LONG × SHORT threshold combo"
                  />
                )}
                {data.optimizer.atr_grid && data.optimizer.atr_grid.length > 0 && (
                  <ParamHeatmap
                    grid={data.optimizer.atr_grid}
                    xKey="ATR_TARGET_MULTIPLE" yKey="ATR_STOP_MULTIPLE"
                    xLabel="TARGET ×ATR" yLabel="STOP ×ATR"
                    title="Stop / Target Multiples"
                    subtitle="Held-out (OOS) profit per stop × target ATR combo"
                  />
                )}
              </div>
            </div>
          ) : null}

          {/* AI4Trade challenges */}
          <ChallengePanel />

          {/* No data */}
          {!data.optimal && !data.results && (
            <div className="card flex flex-col items-center justify-center py-20 gap-3">
              <Clock className="h-8 w-8 text-muted" />
              <p className="text-sm text-muted">No backtest data yet.</p>
              <p className="text-xs text-muted/60">Click <span className="text-brand-cyan font-medium">Run Backtest</span> or <span className="text-bull font-medium">Run Optimizer</span> above to generate results.</p>
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
