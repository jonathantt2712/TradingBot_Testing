'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { RefreshCw, Wifi, WifiOff, ShieldCheck, AlertTriangle } from 'lucide-react'
import { api } from '@/lib/api'
import { usePolling } from '@/lib/usePolling'
import { cn } from '@/lib/utils'
import type { ValidationData, TradeRecord, OhlcBar } from '@/types/trading'
import CandleChart, { type TradeMarker } from '@/components/validation/CandleChart'

const REFRESH_MS = 60_000
const axisTick = { fontSize: 10, fill: '#64748B' }

const VERDICT = {
  edge:         { label: 'EDGE (p<1%)',  cls: 'border-bull/30 bg-bull/10 text-bull' },
  weak:         { label: 'WEAK (p<5%)',  cls: 'border-caution/40 bg-caution/10 text-caution' },
  inconclusive: { label: 'NOT PROVEN',   cls: 'border-bg-border bg-bg-elev text-muted' },
} as const

function StatCard({ label, value, tone }: { label: string; value: string; tone?: 'bull' | 'bear' | 'cyan' }) {
  const color = tone === 'bull' ? 'text-bull' : tone === 'bear' ? 'text-bear' : tone === 'cyan' ? 'text-brand-cyan' : 'text-primary'
  return (
    <div className="card p-4">
      <div className={cn('text-2xl font-bold', color)}>{value}</div>
      <div className="text-xs text-muted mt-1">{label}</div>
    </div>
  )
}

export default function ValidationPage() {
  const [data, setData]       = useState<ValidationData | null>(null)
  const [live, setLive]       = useState(false)
  const [loading, setLoading] = useState(true)

  // Candlestick state: closed trades drive the ticker list + entry/exit markers.
  const [closed, setClosed]   = useState<TradeRecord[]>([])
  const [ticker, setTicker]   = useState<string>('')
  const [bars, setBars]       = useState<OhlcBar[]>([])
  const [barsLoading, setBarsLoading] = useState(false)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api.validation()
      setData(d); setLive(true)
    } catch {
      setLive(false)
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchHistory = useCallback(async () => {
    try {
      const h = await api.history()
      const c = (h || []).filter(t => t.status === 'closed' && t.exit != null)
      setClosed(c)
      setTicker(prev => prev || c[0]?.ticker || '')
    } catch { /* keep empty */ }
  }, [])

  usePolling(fetchData, REFRESH_MS)
  useEffect(() => { fetchHistory() }, [fetchHistory])   // one-time: ticker list + markers

  // Load bars whenever the selected ticker changes.
  useEffect(() => {
    if (!ticker) { setBars([]); return }
    let cancelled = false
    setBarsLoading(true)
    api.bars(ticker, '5Min', 300)
      .then(res => { if (!cancelled) setBars(res?.[ticker] ?? []) })
      .catch(() => { if (!cancelled) setBars([]) })
      .finally(() => { if (!cancelled) setBarsLoading(false) })
    return () => { cancelled = true }
  }, [ticker])

  const tickers = useMemo(
    () => Array.from(new Set(closed.map(t => t.ticker))), [closed])

  const markers = useMemo<TradeMarker[]>(() => {
    const out: TradeMarker[] = []
    for (const t of closed) {
      if (t.ticker !== ticker) continue
      if (t.opened_at && t.entry != null) out.push({ t: t.opened_at, price: t.entry, kind: 'entry' })
      if (t.closed_at && t.exit != null)  out.push({ t: t.closed_at, price: t.exit, kind: 'exit' })
    }
    return out
  }, [closed, ticker])

  const equityRows = useMemo(() =>
    (data?.equity ?? []).map((e, i) => ({ i: i + 1, equity: +e.toFixed(4) })), [data])
  const ddRows = useMemo(() =>
    (data?.drawdown ?? []).map((d, i) => ({ i: i + 1, dd: +(d * 100).toFixed(2) })), [data])

  const rt = data?.randomization_test
  const verdict = VERDICT[data?.verdict ?? 'inconclusive']
  const hasData = (data?.trades ?? 0) > 0

  const pct = (v?: number) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 max-w-[1100px] mx-auto">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-lg font-bold text-primary flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-brand-cyan" /> Validation
          </h1>
          <span className={cn('rounded-full border px-3 py-0.5 text-xs font-bold', verdict.cls)}>{verdict.label}</span>
          {data != null && <span className="text-xs text-muted">{data.trades} closed trades</span>}
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
        Is the edge real or memorised noise? This runs a sign-flip randomization test on the realised
        trade returns (1,000 shuffles) — a low p-value means the track record is hard to explain by luck.
        Equity and the underwater drawdown below are the honest picture.
      </p>

      {data?.sample_warning && (
        <div className="card p-3 flex items-start gap-2 border-caution/40 bg-caution/5">
          <AlertTriangle className="h-4 w-4 text-caution mt-0.5 shrink-0" />
          <span className="text-xs text-caution">{data.sample_warning}</span>
        </div>
      )}

      {!hasData ? (
        <div className="card p-8 text-center text-sm text-muted">
          {data?.message || 'No closed trades yet — the gauntlet runs once the bot has a track record.'}
        </div>
      ) : (
        <>
          {/* Stats */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatCard label="Total return" value={pct(data?.total_return)} tone={(data?.total_return ?? 0) >= 0 ? 'bull' : 'bear'} />
            <StatCard label="Win rate" value={data?.win_rate != null ? `${data.win_rate}%` : '—'} />
            <StatCard label="Profit factor" value={data?.profit_factor != null ? data.profit_factor.toFixed(2) : '—'} />
            <StatCard label="Sharpe / trade" value={data?.per_trade_sharpe != null ? data.per_trade_sharpe.toFixed(2) : '—'} />
            <StatCard label="Max drawdown" value={pct(data?.max_drawdown)} tone="bear" />
            <StatCard label="p-value" value={rt ? rt.p_value.toFixed(3) : '—'} tone={rt && rt.p_value < 0.01 ? 'bull' : 'cyan'} />
          </div>

          {/* Equity curve */}
          <div className="card p-4">
            <div className="text-sm font-semibold text-primary mb-2">Equity curve (× starting capital)</div>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={equityRows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
                  <XAxis dataKey="i" tick={axisTick} />
                  <YAxis tick={axisTick} domain={['auto', 'auto']} />
                  <Tooltip contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', fontSize: 12 }} />
                  <ReferenceLine y={1} stroke="#475569" strokeDasharray="4 4" />
                  <Line type="monotone" dataKey="equity" stroke="#22D3EE" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Underwater drawdown */}
          <div className="card p-4">
            <div className="text-sm font-semibold text-primary mb-2">Underwater — drawdown %</div>
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={ddRows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
                  <XAxis dataKey="i" tick={axisTick} />
                  <YAxis tick={axisTick} />
                  <Tooltip contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', fontSize: 12 }} />
                  <Area type="monotone" dataKey="dd" stroke="#EF4444" fill="#EF4444" fillOpacity={0.35} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Candlesticks with entry/exit markers — verify WHERE it traded */}
          {tickers.length > 0 && (
            <div className="card p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="text-sm font-semibold text-primary">
                  Where it traded — entries ▲ / exits ▼
                </div>
                <select
                  value={ticker}
                  onChange={e => setTicker(e.target.value)}
                  className="bg-bg-elev border border-bg-border rounded px-2 py-1 text-xs text-primary"
                >
                  {tickers.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              {barsLoading
                ? <div className="h-72 flex items-center justify-center text-sm text-muted">Loading {ticker} bars…</div>
                : <CandleChart bars={bars} markers={markers} />}
              <div className="text-[11px] text-muted mt-1">
                5-min bars (recent). Markers snap to the bar at each fill time. Bars come live from
                Alpaca; older trades may fall outside the available window.
              </div>
            </div>
          )}

          <p className="text-[11px] text-muted">
            Note: the strong 1,000× price-permutation and walk-forward-permutation tests run offline
            via <code>validation.run --bars</code>
            {' '}(they need a vectorised signal proxy + bar data). This view is the realised-record screen.
          </p>
        </>
      )}
    </div>
  )
}
