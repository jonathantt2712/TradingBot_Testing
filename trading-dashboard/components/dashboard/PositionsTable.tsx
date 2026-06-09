'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import {
  X, Loader2, ChevronDown, ChevronUp,
  TrendingUp, TrendingDown, Minus,
  AlertTriangle, PlusCircle, MinusCircle, ShieldCheck,
  RefreshCw,
} from 'lucide-react'
import { toast } from 'sonner'
import { cn, formatPrice } from '@/lib/utils'
import type { AlpacaPosition } from '@/lib/alpaca'
import type { TradeRecommendation } from '@/types/trading'

interface Bar { o: number; h: number; l: number; c: number; v: number; t: string }
type RecAction = 'HOLD' | 'ADD' | 'TAKE_PROFIT' | 'REDUCE' | 'EXIT'
interface Recommendation { action: RecAction; reason: string; addQty?: number }
interface Props { positions: AlpacaPosition[]; onClosed?: (symbol: string) => void }

interface PositionContext {
  ticker:          string
  direction:       string
  entry:           number
  stop_loss:       number | null
  take_profit:     number | null
  qty:             number
  composite_score: number | null
}

/** Build a minimal TradeRecommendation from server-side context. */
function _ctxToRec(ctx: PositionContext): TradeRecommendation {
  return {
    id:              ctx.ticker,
    ticker:          ctx.ticker,
    direction:       ctx.direction as 'LONG' | 'SHORT',
    composite_score: ctx.composite_score ?? 50,
    agent_used:      false,
    rationale:       '',
    regime:          'neutral',
    sector:          '',
    scanned_at:      '',
    expires_at:      '',
    reeval_count:    0,
    hot_sector:      false,
    evaluations:     [],
    timestamp:       '',
    risk: {
      entry:       ctx.entry,
      stop_loss:   ctx.stop_loss ?? 0,
      take_profit: ctx.take_profit ?? 0,
      qty:         ctx.qty,
      risk_reward: 0,
      dollar_risk: 0,
    },
  } as TradeRecommendation
}

function computeRec(pos: AlpacaPosition, tradeRec?: TradeRecommendation | null): Recommendation {
  const current = parseFloat(pos.current_price)
  const entry   = parseFloat(pos.avg_entry_price)
  const pnlPct  = parseFloat(pos.unrealized_plpc) * 100
  const isLong  = pos.side === 'long'

  if (!tradeRec) {
    if (pnlPct > 3)  return { action: 'TAKE_PROFIT', reason: `Up ${pnlPct.toFixed(1)}% - consider locking in` }
    if (pnlPct < -2) return { action: 'REDUCE',      reason: `Down ${Math.abs(pnlPct).toFixed(1)}% - reduce risk` }
    return { action: 'HOLD', reason: 'Trade developing - no target/stop data' }
  }

  const { take_profit: tp, stop_loss: sl, qty, risk_reward: rr } = tradeRec.risk
  const score = tradeRec.composite_score
  const tpTotal    = Math.abs(tp - entry)
  const progress   = isLong ? (current - entry) : (entry - current)
  const progressPct = tpTotal > 0 ? (progress / tpTotal) * 100 : 0
  const distToSl   = isLong ? ((current - sl) / current) * 100 : ((sl - current) / current) * 100

  if (progressPct >= 85)
    return { action: 'TAKE_PROFIT', reason: `${progressPct.toFixed(0)}% of target reached - lock in gains` }
  if (progressPct >= 50 && score > 62 && rr >= 2) {
    const addQty = Math.max(1, Math.floor(qty * 0.5))
    return { action: 'ADD', reason: `Trend confirmed at ${progressPct.toFixed(0)}% to target, score ${score}`, addQty }
  }
  if (distToSl < 0.4)
    return { action: 'EXIT', reason: `${distToSl.toFixed(2)}% from stop - exit now` }
  if (progressPct < -30)
    return { action: 'REDUCE', reason: `Retracing - ${Math.abs(progressPct).toFixed(0)}% against target` }
  return {
    action: 'HOLD',
    reason: progressPct >= 0 ? `${progressPct.toFixed(0)}% toward target` : `Flat - ${Math.abs(progressPct).toFixed(0)}% below entry`,
  }
}

function PriceChart({ bars, entry, tp, sl, isLong }: { bars: Bar[]; entry: number; tp?: number; sl?: number; isLong: boolean }) {
  if (!bars.length) return <div className="flex items-center justify-center h-24 text-xs text-muted">Loading chart...</div>
  const prices  = bars.map(b => b.c)
  const allVals = [...prices, entry, ...(tp ? [tp] : []), ...(sl ? [sl] : [])]
  const hi      = Math.max(...allVals) * 1.002
  const lo      = Math.min(...allVals) * 0.998
  const range   = hi - lo || 1
  const W = 420; const H = 80
  const toY = (v: number) => H - ((v - lo) / range) * H
  const toX = (i: number) => (i / (prices.length - 1)) * W
  const linePath = prices.map((p, i) => `${i === 0 ? 'M' : 'L'}${toX(i).toFixed(1)},${toY(p).toFixed(1)}`).join(' ')
  const last      = prices[prices.length - 1]
  const lineColor = last >= entry ? '#22C55E' : '#F87171'
  const fillPath  = `${linePath} L${W},${toY(entry)} L0,${toY(entry)} Z`
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-20 overflow-visible">
      {tp && <line x1="0" y1={toY(tp)} x2={W} y2={toY(tp)} stroke="#22C55E" strokeWidth="1" strokeDasharray="4 3" opacity="0.7" />}
      {sl && <line x1="0" y1={toY(sl)} x2={W} y2={toY(sl)} stroke="#F87171" strokeWidth="1" strokeDasharray="4 3" opacity="0.7" />}
      <line x1="0" y1={toY(entry)} x2={W} y2={toY(entry)} stroke="#94A3B8" strokeWidth="1" strokeDasharray="2 4" opacity="0.6" />
      <path d={fillPath} fill={lineColor} opacity="0.1" />
      <path d={linePath} fill="none" stroke={lineColor} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={W} cy={toY(last)} r="3" fill={lineColor} />
      {tp && <text x={W + 4} y={toY(tp) + 4} fontSize="8" fill="#22C55E" opacity="0.8">TP</text>}
      {sl && <text x={W + 4} y={toY(sl) + 4} fontSize="8" fill="#F87171" opacity="0.8">SL</text>}
    </svg>
  )
}

const REC_STYLES: Record<RecAction, { color: string; icon: React.ReactNode; label: string }> = {
  HOLD:        { color: 'border-subtle/30 text-subtle bg-bg-hover',              icon: <Minus         className="h-3 w-3" />, label: 'Hold'        },
  ADD:         { color: 'border-brand-cyan/40 text-brand-cyan bg-brand-cyan/10', icon: <PlusCircle    className="h-3 w-3" />, label: 'Add More'    },
  TAKE_PROFIT: { color: 'border-bull/40 text-bull bg-bull/10',                   icon: <TrendingUp    className="h-3 w-3" />, label: 'Take Profit' },
  REDUCE:      { color: 'border-caution/40 text-caution bg-caution/10',          icon: <MinusCircle   className="h-3 w-3" />, label: 'Reduce'      },
  EXIT:        { color: 'border-bear/40 text-bear bg-bear/10',                   icon: <AlertTriangle className="h-3 w-3" />, label: 'Exit Now'    },
}

function RecBadge({ rec }: { rec: Recommendation }) {
  const s = REC_STYLES[rec.action]
  return (
    <div className={cn('flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold whitespace-nowrap', s.color)}>
      {s.icon}{s.label}
      {rec.addQty != null && <span className="ml-0.5 opacity-80">+{rec.addQty}</span>}
    </div>
  )
}

function PositionRow({ position, tradeRec, onClose, closing }: {
  position: AlpacaPosition; tradeRec?: TradeRecommendation | null; onClose: (sym: string) => void; closing: string | null
}) {
  const [expanded,    setExpanded]    = useState(true)
  const [bars,        setBars]        = useState<Bar[]>([])
  const [loadingBars, setLoadingBars] = useState(false)

  const isLong    = position.side === 'long'
  const pnl       = parseFloat(position.unrealized_pl)
  const pnlPct    = parseFloat(position.unrealized_plpc) * 100
  const totalCost = parseFloat(position.qty) * parseFloat(position.current_price)
  const isClosing = closing === position.symbol
  const rec       = computeRec(position, tradeRec)

  const fetchBars = useCallback(async () => {
    if (bars.length || loadingBars) return
    setLoadingBars(true)
    try {
      const today = new Date(); today.setHours(9, 30, 0, 0)
      const start = encodeURIComponent(today.toISOString())
      const res   = await fetch(`/api/alpaca/bars?symbols=${position.symbol}&timeframe=5Min&start=${start}&limit=78`)
      if (res.ok) { const data = await res.json(); setBars(data[position.symbol] ?? []) }
    } catch { /* offline */ } finally { setLoadingBars(false) }
  }, [position.symbol, bars.length, loadingBars])

  useEffect(() => { if (expanded) fetchBars() }, [expanded, fetchBars])

  return (
    <div className="border-b border-bg-border/50 last:border-0">
      <div className="flex items-center gap-3 px-4 py-3 hover:bg-bg-hover/50 transition-colors">
        <button onClick={() => setExpanded(v => !v)} className="text-muted hover:text-subtle transition-colors shrink-0">
          {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </button>
        <div className="w-20 shrink-0">
          <span className="font-mono font-bold text-sm text-primary">{position.symbol}</span>
          <span className={cn('ml-1.5 rounded px-1 py-0.5 text-[10px] font-semibold', isLong ? 'bg-bull/10 text-bull' : 'bg-bear/10 text-bear')}>
            {position.side.toUpperCase()}
          </span>
        </div>
        <span className="w-10 text-xs font-mono text-subtle shrink-0">{position.qty}</span>
        <div className="flex items-center gap-1.5 text-xs font-mono text-subtle shrink-0">
          <span>{formatPrice(parseFloat(position.avg_entry_price))}</span>
          <span className="text-muted">to</span>
          <span className="text-primary">{formatPrice(parseFloat(position.current_price))}</span>
        </div>
        <div className="flex flex-col items-end shrink-0 text-right">
          <span className="text-[10px] text-muted">Cost</span>
          <span className="text-xs font-mono font-semibold text-subtle">
            ${totalCost.toLocaleString('en-US', { maximumFractionDigits: 0 })}
          </span>
        </div>
        <div className={cn('ml-auto flex flex-col items-end shrink-0', pnl >= 0 ? 'text-bull' : 'text-bear')}>
          <span className="text-xs font-mono font-semibold">{pnl >= 0 ? '+' : ''}${Math.abs(pnl).toFixed(2)}</span>
          <span className="text-[10px] font-mono opacity-80">{pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%</span>
        </div>
        <div className="shrink-0 ml-2"><RecBadge rec={rec} /></div>
        <button
          onClick={() => onClose(position.symbol)}
          disabled={isClosing}
          className="shrink-0 ml-1 flex items-center gap-1 rounded-md border border-bear/30 bg-bear/10 px-2 py-1 text-[10px] font-semibold text-bear hover:bg-bear/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isClosing ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : <X className="h-2.5 w-2.5" />}
          Close
        </button>
      </div>

      {expanded && (
        <div className="px-4 pb-4 space-y-3 bg-bg-base/50">
          <div className={cn('flex items-start gap-2 rounded-lg border px-3 py-2 text-xs', REC_STYLES[rec.action].color)}>
            {REC_STYLES[rec.action].icon}
            <div>
              <span className="font-semibold">{REC_STYLES[rec.action].label}</span>
              <span className="text-muted ml-1.5">- {rec.reason}</span>
              {rec.addQty != null && <span className="ml-1 font-semibold">Add {rec.addQty} share{rec.addQty > 1 ? 's' : ''} at market.</span>}
            </div>
          </div>
          {tradeRec && (
            <div className="grid grid-cols-3 gap-2 text-center text-xs">
              <div className="rounded-lg bg-bg-card px-3 py-2">
                <p className="text-[10px] text-muted mb-0.5">Entry</p>
                <p className="font-mono font-semibold text-subtle">{formatPrice(tradeRec.risk.entry)}</p>
              </div>
              <div className="rounded-lg bg-bg-card px-3 py-2">
                <p className="text-[10px] text-muted mb-0.5">Stop Loss</p>
                <p className="font-mono font-semibold text-bear">{formatPrice(tradeRec.risk.stop_loss)}</p>
              </div>
              <div className="rounded-lg bg-bg-card px-3 py-2">
                <p className="text-[10px] text-muted mb-0.5">Take Profit</p>
                <p className="font-mono font-semibold text-bull">{formatPrice(tradeRec.risk.take_profit)}</p>
              </div>
            </div>
          )}
          <div className="rounded-lg bg-bg-card px-3 pt-3 pb-1">
            {loadingBars ? (
              <div className="flex items-center justify-center h-20 gap-2 text-xs text-muted">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading chart...
              </div>
            ) : (
              <PriceChart bars={bars} entry={parseFloat(position.avg_entry_price)} tp={tradeRec?.risk.take_profit} sl={tradeRec?.risk.stop_loss} isLong={isLong} />
            )}
            <div className="flex items-center justify-between text-[10px] text-muted mt-1 pb-1">
              <span>Today (5-min)</span>
              <span className="flex items-center gap-2">
                <span className="flex items-center gap-1"><span className="inline-block w-4 h-px border-t border-dashed border-bull/60" /> TP</span>
                <span className="flex items-center gap-1"><span className="inline-block w-4 h-px border-t border-dashed border-bear/60" /> SL</span>
                <span className="flex items-center gap-1"><span className="inline-block w-4 h-px border-t border-dashed border-subtle/40" /> Entry</span>
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

const REFRESH_MS = 30_000

export function PositionsTable({ positions, onClosed }: Props) {
  const [closing,     setClosing]     = useState<string | null>(null)
  const [tradeRecs,   setTradeRecs]   = useState<TradeRecommendation[]>([])
  const [refreshing,  setRefreshing]  = useState(false)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const router = useRouter()

  // Fetch TP/SL context from bot server; fall back to localStorage
  const fetchCtx = useCallback(async () => {
    try {
      const r = await fetch('/api/bot/positions/context')
      if (!r.ok) throw new Error('not ok')
      const ctx: PositionContext[] = await r.json()
      if (ctx.length > 0) {
        setTradeRecs(ctx.map(_ctxToRec))
        return
      }
    } catch { /* bot offline */ }
    // Fall back to localStorage
    try {
      const raw = localStorage.getItem('executed_trade_recs')
      if (raw) setTradeRecs(JSON.parse(raw) as TradeRecommendation[])
    } catch { /* ignore */ }
  }, [])

  // Initial fetch
  useEffect(() => { fetchCtx() }, [fetchCtx])

  // Auto-refresh P&L (via server component rerender) + context every 30 s
  useEffect(() => {
    const id = setInterval(() => {
      router.refresh()
      fetchCtx()
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [router, fetchCtx])

  // Manual refresh handler
  async function handleManualRefresh() {
    setRefreshing(true)
    router.refresh()
    await fetchCtx()
    setLastRefresh(new Date())
    setRefreshing(false)
  }

  function findRec(symbol: string): TradeRecommendation | null {
    return tradeRecs.find(r => r.ticker === symbol) ?? null
  }

  async function handleClose(symbol: string) {
    if (!confirm(`Close entire ${symbol} position?`)) return
    setClosing(symbol)
    try {
      const res  = await fetch(`/api/alpaca/positions/${encodeURIComponent(symbol)}`, { method: 'DELETE' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.message ?? `${res.status}`)
      toast.success(`${symbol} position closed`, { description: `Order ID: ${data.order?.id ?? 'submitted'}` })
      onClosed?.(symbol)
      router.refresh()
    } catch (err: any) {
      toast.error(`Failed to close ${symbol}`, { description: err.message })
    } finally {
      setClosing(null)
    }
  }

  const totalCostAll = positions.reduce((s, p) => s + parseFloat(p.qty) * parseFloat(p.current_price), 0)
  const totalPnlAll  = positions.reduce((s, p) => s + parseFloat(p.unrealized_pl), 0)
  const avgPnlPct    = positions.length
    ? positions.reduce((s, p) => s + parseFloat(p.unrealized_plpc) * 100, 0) / positions.length
    : 0

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center justify-between px-5 py-4 border-b border-bg-border">
        <h2 className="text-sm font-semibold text-primary">Open Positions</h2>
        <div className="flex items-center gap-2">
          <span className="badge bg-brand-cyan/10 border-brand-cyan/20 text-brand-cyan">{positions.length} open</span>
          {lastRefresh && (
            <span className="text-[10px] text-muted">{lastRefresh.toLocaleTimeString()}</span>
          )}
          <button
            onClick={handleManualRefresh}
            disabled={refreshing}
            className="flex items-center gap-1 rounded-md p-1 text-muted hover:text-subtle hover:bg-bg-hover transition-colors disabled:opacity-40"
            title="Refresh positions"
          >
            <RefreshCw className={cn('h-3 w-3', refreshing && 'animate-spin')} />
          </button>
          <span className="text-[10px] text-muted">Bracket orders auto-close on TP/SL - EOD if unfilled</span>
        </div>
      </div>

      {positions.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 text-center">
          <ShieldCheck className="h-8 w-8 text-muted mb-2" />
          <p className="text-sm text-muted">No open positions</p>
          <p className="text-xs text-muted/60 mt-1">Execute a trade to see it here</p>
        </div>
      ) : (
        <div>
          <div className="flex items-center gap-3 px-4 py-1.5 text-[10px] font-medium text-muted border-b border-bg-border/50">
            <span className="w-3.5 shrink-0" />
            <span className="w-20 shrink-0">Symbol</span>
            <span className="w-10 shrink-0">Qty</span>
            <span className="shrink-0">Entry / Current</span>
            <span className="shrink-0">Cost</span>
            <span className="ml-auto shrink-0">P&amp;L</span>
            <span className="ml-2 shrink-0">Signal</span>
            <span className="ml-1 w-14 shrink-0" />
          </div>
          {positions.map(pos => (
            <PositionRow
              key={pos.symbol}
              position={pos}
              tradeRec={findRec(pos.symbol)}
              onClose={handleClose}
              closing={closing}
            />
          ))}
          <div className="flex items-center gap-3 px-4 py-2.5 border-t border-bg-border bg-bg-hover/30 text-xs font-semibold">
            <span className="w-3.5 shrink-0" />
            <span className="w-20 shrink-0 text-subtle">{positions.length} position{positions.length !== 1 ? 's' : ''}</span>
            <span className="w-10 shrink-0 text-muted font-normal">
              {positions.reduce((s, p) => s + parseFloat(p.qty), 0)} sh
            </span>
            <span className="shrink-0" />
            <div className="flex flex-col items-end shrink-0">
              <span className="text-[10px] text-muted font-normal">Total Cost</span>
              <span className="font-mono text-subtle">${totalCostAll.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
            </div>
            <div className={cn('ml-auto flex flex-col items-end shrink-0', totalPnlAll >= 0 ? 'text-bull' : 'text-bear')}>
              <span className="font-mono">{totalPnlAll >= 0 ? '+' : ''}${Math.abs(totalPnlAll).toFixed(2)}</span>
              <span className="text-[10px] font-mono opacity-80">{avgPnlPct >= 0 ? '+' : ''}{avgPnlPct.toFixed(2)}% avg</span>
            </div>
            <div className="ml-2 shrink-0 w-20" />
            <div className="ml-1 w-14 shrink-0" />
          </div>
        </div>
      )}
    </div>
  )
}
