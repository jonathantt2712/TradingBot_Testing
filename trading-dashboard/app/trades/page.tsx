'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { TradeCard }       from '@/components/trades/TradeCard'
import { ConfirmModal }    from '@/components/trades/ConfirmModal'
import { RationaleModal }  from '@/components/trades/RationaleModal'
import { ExecutionModeToggle } from '@/components/trades/ExecutionModeToggle'
import { RegimeIndicator } from '@/components/dashboard/RegimeIndicator'
import { demoRegime, api } from '@/lib/api'
import type { TradeRecommendation, RegimeInfo } from '@/types/trading'
import { RefreshCw, Filter, Wifi, WifiOff, ChevronDown, ChevronUp, CheckCircle2, ShoppingCart, Loader2 } from 'lucide-react'

import { cn } from '@/lib/utils'
import { toast } from 'sonner'

const REFRESH_MS = 30_000
const PRICE_MS   = 15_000
const KEY_IDS    = 'executed_trade_ids'
const KEY_RECS   = 'executed_trade_recs'
const KEY_DATE   = 'executed_trade_date'

function _todayUTC(): string {
  return new Date().toISOString().slice(0, 10) // 'YYYY-MM-DD'
}

/** Reset IDs and recs if the stored date differs from today (new trading day). */
function _maybeDailyReset(): void {
  try {
    const stored = localStorage.getItem(KEY_DATE)
    const today  = _todayUTC()
    if (stored !== today) {
      localStorage.removeItem(KEY_IDS)
      localStorage.removeItem(KEY_RECS)
      localStorage.setItem(KEY_DATE, today)
    }
  } catch { /* ignore */ }
}

function loadIds(): Set<string> {
  if (typeof window === 'undefined') return new Set()
  try {
    _maybeDailyReset()
    const raw = localStorage.getItem(KEY_IDS)
    return raw ? new Set(JSON.parse(raw) as string[]) : new Set()
  } catch { return new Set() }
}
function saveIds(ids: Set<string>) {
  try {
    localStorage.setItem(KEY_DATE, _todayUTC())
    localStorage.setItem(KEY_IDS, JSON.stringify([...ids]))
  } catch {}
}
function loadExecRecs(): TradeRecommendation[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = localStorage.getItem(KEY_RECS)
    return raw ? (JSON.parse(raw) as TradeRecommendation[]) : []
  } catch { return [] }
}
function saveExecRecs(recs: TradeRecommendation[]) {
  try { localStorage.setItem(KEY_RECS, JSON.stringify(recs)) } catch {}
}

function byScore(a: TradeRecommendation, b: TradeRecommendation) {
  return b.composite_score - a.composite_score
}

/** Recommendation ids are regenerated on every scan, so identify a trade
 *  by ticker+direction to track "already executed" across re-scans. */
function tradeKey(t: TradeRecommendation): string {
  return `${t.ticker}-${t.direction}`
}

export default function TradesPage() {


  const [selected,     setSelected]     = useState<TradeRecommendation | null>(null)
  const [infoTrade,    setInfoTrade]    = useState<TradeRecommendation | null>(null)
  const [filter,       setFilter]       = useState<'all' | 'LONG' | 'SHORT'>('all')
  const [trades,       setTrades]       = useState<TradeRecommendation[]>([])
  const [regime,       setRegime]       = useState<RegimeInfo>(demoRegime())
  const [prices,       setPrices]       = useState<Record<string, number>>({})
  const [loading,      setLoading]      = useState(false)
  const [live,         setLive]         = useState(false)
  const [lastFetch,    setLastFetch]    = useState<Date | null>(null)
  const [executedIds,  setExecutedIds]  = useState<Set<string>>(new Set())
  const [executedRecs, setExecutedRecs] = useState<TradeRecommendation[]>([])
  const [showExecuted, setShowExecuted] = useState(false)
  const [buyingAll,    setBuyingAll]    = useState(false)

  const tradesRef = useRef<TradeRecommendation[]>([])
  tradesRef.current = trades

  useEffect(() => {
    setExecutedIds(loadIds())
    setExecutedRecs(loadExecRecs())
  }, [])

  const fetchPrices = useCallback(async (recs: TradeRecommendation[]) => {
    if (!recs.length) return
    const syms = [...new Set(recs.map(t => t.ticker))]
    try {
      const snaps = await api.snapshots(syms)
      const map: Record<string, number> = {}
      for (const [sym, snap] of Object.entries(snaps as any)) {
        const s = snap as any
        const price = s?.latestTrade?.p ?? s?.latestQuote?.ap ?? s?.dailyBar?.c
        if (price) map[sym] = price
      }
      setPrices(map)
    } catch { /* offline */ }
  }, [])

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [recs, reg] = await Promise.allSettled([api.recommendations(), api.regime()])
      const now = Date.now()
      const newRecs = recs.status === 'fulfilled'
        ? recs.value
            .filter(t => !t.expires_at || new Date(t.expires_at).getTime() > now)
            .sort(byScore)
        : []
      setTrades(newRecs)
      setLive(recs.status === 'fulfilled')
      if (reg.status === 'fulfilled') setRegime(reg.value)
      fetchPrices(newRecs)
    } catch {
      setLive(false)
    } finally {
      setLoading(false)
      setLastFetch(new Date())
    }
  }, [fetchPrices])

  useEffect(() => {
    fetchData()
    const recId   = setInterval(fetchData, REFRESH_MS)
    const priceId = setInterval(() => fetchPrices(tradesRef.current), PRICE_MS)
    return () => { clearInterval(recId); clearInterval(priceId) }
  }, [fetchData, fetchPrices])

  async function handleBuyAll() {
    if (!active.length || buyingAll) return
    setBuyingAll(true)

    // Pre-flight: check open position count vs. max
    let tradesToExecute = active
    try {
      const ssRes = await fetch('/api/bot/scan-stats', { cache: 'no-store' })
      if (ssRes.ok) {
        const ss = await ssRes.json()
        const openPos = ss.open_positions ?? 0
        const maxPos  = ss.max_positions  ?? Infinity
        if (openPos >= maxPos) {
          toast.error('Position limit reached', {
            description: `Already at max positions (${openPos}/${maxPos}). No new trades.`,
          })
          setBuyingAll(false)
          return
        }
        const slots = maxPos - openPos
        if (slots < tradesToExecute.length) {
          tradesToExecute = tradesToExecute.slice(0, slots)
          toast.info(`Position cap: executing only ${slots} of ${active.length} trades`, {
            description: `${openPos} open, max ${maxPos}`,
          })
        }
      }
    } catch { /* if scan-stats unavailable, proceed with all */ }

    let succeeded = 0
    let failed    = 0
    for (const trade of tradesToExecute) {
      try {
        const res = await api.execute({
          recommendation_id: trade.id,
          ticker:          trade.ticker,
          direction:       trade.direction,
          qty:             trade.risk.qty,
          entry:           trade.risk.entry,
          stop_loss:       trade.risk.stop_loss,
          take_profit:     trade.risk.take_profit,
          composite_score: trade.composite_score,
        })
        const newIds = new Set(executedIds).add(tradeKey(trade))
        setExecutedIds(newIds)
        saveIds(newIds)
        const newRecs = [trade, ...loadExecRecs()]
        saveExecRecs(newRecs)
        setExecutedRecs(newRecs)
        succeeded++
        toast.success(`${trade.direction} ${trade.ticker}`, {
          description: `${res.qty} shares @ ${trade.risk.entry}`,
        })
      } catch (err: any) {
        if (err?.status === 409) {
          // Idempotency dedup — already submitted within 30s, mark as executed
          const newIds = new Set(executedIds).add(tradeKey(trade))
          setExecutedIds(newIds)
          saveIds(newIds)
          toast.info(`${trade.ticker} already submitted`, { description: 'Skipped duplicate within 30s' })
        } else if (err?.status === 422) {
          // Account too small to size this trade — skip, not a hard failure
          failed++
          toast.error(`${trade.ticker} skipped`, { description: err.message })
        } else {
          failed++
          toast.error(`Failed: ${trade.ticker}`, { description: err?.message || undefined })
        }
      }
    }
    setBuyingAll(false)
    setShowExecuted(true)
    if (succeeded > 0) {
      toast.success(`Bought all — ${succeeded} trade${succeeded > 1 ? 's' : ''} submitted`, {
        description: failed > 0 ? `${failed} failed` : undefined,
      })
    }
  }

  function handleExecuted(trade: TradeRecommendation) {
    const newIds = new Set(executedIds).add(tradeKey(trade))
    setExecutedIds(newIds)
    saveIds(newIds)
    const newRecs = [trade, ...executedRecs]
    setExecutedRecs(newRecs)
    saveExecRecs(newRecs)
    setSelected(null)
    setShowExecuted(true)
  }

  const active = trades
    .filter(t => !executedIds.has(tradeKey(t)))
    .filter(t => filter === 'all' || t.direction === filter)

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 md:space-y-6 max-w-[1400px]">

      {/* Header — stacks on mobile */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-lg md:text-xl font-bold text-primary">Trade Recommendations</h1>
          <p className="text-xs text-muted mt-0.5">
            {active.length} signals
            {lastFetch && ` · ${lastFetch.toLocaleTimeString()}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {live
            ? <span className="flex items-center gap-1.5 text-xs text-bull"><Wifi className="h-3 w-3" /> Live</span>
            : <span className="flex items-center gap-1.5 text-xs text-caution"><WifiOff className="h-3 w-3" /> Demo</span>
          }
          {/* Execution mode: manual approval vs auto-execute */}
          <ExecutionModeToggle />
          {/* Filter pills */}
          <div className="flex items-center gap-1 rounded-lg border border-bg-border p-0.5">
            {(['all', 'LONG', 'SHORT'] as const).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={cn(
                  'rounded-md px-2.5 py-1 text-xs font-medium transition-all',
                  filter === f
                    ? f === 'LONG'  ? 'bg-bull/15 text-bull'
                    : f === 'SHORT' ? 'bg-bear/15 text-bear'
                    : 'bg-brand-cyan/10 text-brand-cyan'
                    : 'text-muted hover:text-subtle',
                )}
              >
                {f === 'all' ? 'All' : f}
              </button>
            ))}
          </div>
          {active.length > 0 && (
            <button
              onClick={handleBuyAll}
              disabled={buyingAll}
              className={cn(
                'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all',
                'bg-brand-cyan/15 border border-brand-cyan/30 text-brand-cyan hover:bg-brand-cyan/25',
                'disabled:opacity-50 disabled:cursor-not-allowed',
              )}
            >
              {buyingAll ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShoppingCart className="h-3.5 w-3.5" />}
              {buyingAll ? 'Buying...' : `Buy All (${active.length})`}
            </button>
          )}
          <button onClick={fetchData} className="btn-ghost text-xs" disabled={loading}>
            <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_220px]">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 content-start">
          {active.length === 0 ? (
            <div className="col-span-full card flex flex-col items-center justify-center py-16 text-center">
              <Filter className="h-8 w-8 text-muted mb-3" />
              <p className="text-sm text-muted">
                {trades.length === 0 ? 'No available data' : 'No signals match the current filter.'}
              </p>
            </div>
          ) : (
            active.map((t, i) => (
              <div key={t.id} className="relative">
                <span className={cn(
                  'absolute -top-2 -left-2 z-10 flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold shadow',
                  i === 0 ? 'bg-brand-cyan text-bg-base'
                  : i === 1 ? 'bg-brand-cyan/50 text-primary'
                  : 'bg-bg-hover text-muted',
                )}>
                  {i + 1}
                </span>
                <TradeCard
                  trade={t}
                  onExecute={setSelected}
                  onInfo={setInfoTrade}
                  currentPrice={prices[t.ticker]}
                />
              </div>
            ))
          )}
        </div>
        <RegimeIndicator regime={regime} />
      </div>

      {executedRecs.length > 0 && (
        <div className="space-y-3 border-t border-bg-border pt-4">
          <button
            onClick={() => setShowExecuted(v => !v)}
            className="flex items-center gap-2 text-sm font-semibold text-subtle hover:text-primary transition-colors"
          >
            <CheckCircle2 className="h-4 w-4 text-bull" />
            Executed Today
            <span className="rounded-full bg-bull/15 px-2 py-0.5 text-[10px] font-bold text-bull">
              {executedRecs.length}
            </span>
            {showExecuted
              ? <ChevronUp className="h-3.5 w-3.5 text-muted" />
              : <ChevronDown className="h-3.5 w-3.5 text-muted" />
            }
          </button>

          {showExecuted && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {executedRecs.map(t => (
                <div key={`exec-${t.id}`} className="relative pointer-events-none select-none opacity-55">
                  <div className="absolute inset-0 z-10 flex items-center justify-center rounded-xl">
                    <span className="flex items-center gap-1.5 rounded-full bg-bull/20 px-3 py-1 text-xs font-semibold text-bull border border-bull/30 shadow">
                      <CheckCircle2 className="h-3 w-3" /> Executed
                    </span>
                  </div>
                  <TradeCard
                    trade={t}
                    onExecute={() => {}}
                    onInfo={setInfoTrade}
                    currentPrice={prices[t.ticker]}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <ConfirmModal
        key={selected?.id ?? 'none'}
        trade={selected}
        onClose={() => setSelected(null)}
        onDone={() => selected && handleExecuted(selected)}
      />

      <RationaleModal
        trade={infoTrade}
        onClose={() => setInfoTrade(null)}
      />
    </div>
  )
}
