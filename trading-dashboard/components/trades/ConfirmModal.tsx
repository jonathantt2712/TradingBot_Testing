'use client'
import { useState, useEffect } from 'react'
import { X, AlertTriangle, CheckCircle2, Loader2, ArrowUpRight, ArrowDownLeft, Clock, BanknoteIcon } from 'lucide-react'
import { cn, formatPrice, regimeLabel, regimeColor } from '@/lib/utils'
import { api } from '@/lib/api'
import { toast } from 'sonner'
import type { TradeRecommendation } from '@/types/trading'

interface Props {
  trade:    TradeRecommendation | null
  onClose:  () => void
  onDone:   () => void
}

interface PreflightState {
  loading:       boolean
  marketOpen:    boolean | null
  nextOpen:      string | null
  buyingPower:   number | null
}

function formatNextOpen(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })
}

export function ConfirmModal({ trade, onClose, onDone }: Props) {
  const [loading,   setLoading]   = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [preflight, setPreflight] = useState<PreflightState>({ loading: true, marketOpen: null, nextOpen: null, buyingPower: null })

  useEffect(() => {
    if (!trade) return
    setPreflight({ loading: true, marketOpen: null, nextOpen: null, buyingPower: null })
    Promise.allSettled([
      fetch('/api/alpaca/clock').then(r => r.ok ? r.json() : null),
      fetch('/api/alpaca/account').then(r => r.ok ? r.json() : null),
    ]).then(([clockRes, accountRes]) => {
      const clock   = clockRes.status   === 'fulfilled' ? clockRes.value   : null
      const account = accountRes.status === 'fulfilled' ? accountRes.value : null
      setPreflight({
        loading:     false,
        marketOpen:  clock?.is_open   ?? null,
        nextOpen:    clock?.next_open ?? null,
        buyingPower: account?.buying_power != null ? parseFloat(account.buying_power) : null,
      })
    })
  }, [trade])

  if (!trade) return null

  const tradeCost        = trade.risk.qty * trade.risk.entry
  const insufficientFunds = preflight.buyingPower !== null && preflight.buyingPower < tradeCost
  const marketClosed     = preflight.marketOpen === false
  const blocked          = marketClosed || insufficientFunds

  const isLong = trade.direction === 'LONG'

  async function handleExecute() {
    if (!trade) return
    setLoading(true)
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
      setConfirmed(true)
      toast.success(`Trade executed: ${trade.direction} ${res.qty}x ${trade.ticker}`, {
        description: `Order ID: ${res.order_id}`,
      })
      setTimeout(() => { onDone(); onClose() }, 1500)
    } catch (err: any) {
      toast.error('Execution failed', { description: err.message })
    } finally {
      setLoading(false)
    }
  }

  const dirColor = isLong ? 'text-bull' : 'text-bear'
  const dirBg    = isLong ? 'border-bull/30' : 'border-bear/30'
  const totalCost = trade.risk.qty * trade.risk.entry

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(2,6,23,0.85)', backdropFilter: 'blur(8px)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className={cn('w-full max-w-md rounded-2xl border bg-bg-card shadow-2xl animate-slide-up', dirBg)}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-bg-border px-6 py-4">
          <div className="flex items-center gap-3">
            <div className={cn('flex h-9 w-9 items-center justify-center rounded-lg', isLong ? 'bg-bull/15' : 'bg-bear/15')}>
              {isLong ? <ArrowUpRight className="h-5 w-5 text-bull" /> : <ArrowDownLeft className="h-5 w-5 text-bear" />}
            </div>
            <div>
              <h2 className="text-sm font-semibold text-primary">Confirm Trade</h2>
              <p className="text-xs text-muted">Review details and click to execute</p>
            </div>
          </div>
          <button onClick={onClose} className="text-muted hover:text-primary transition-colors">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-6 py-4 space-y-4">
          <div className="flex items-center justify-between rounded-xl bg-bg-base px-4 py-3">
            <div>
              <span className={cn('ticker-mono text-2xl', dirColor)}>{trade.ticker}</span>
              <span className={cn('ml-2 text-sm font-semibold', dirColor)}>{trade.direction}</span>
            </div>
            <div className="text-right">
              <p className="text-xs text-muted">Composite</p>
              <p className={cn('text-lg font-bold font-mono', dirColor)}>{trade.composite_score.toFixed(0)}</p>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-2">
            {[
              { label: 'Entry',  value: formatPrice(trade.risk.entry),      cls: 'text-primary' },
              { label: 'Stop',   value: formatPrice(trade.risk.stop_loss),  cls: 'text-bear'    },
              { label: 'Target', value: formatPrice(trade.risk.take_profit),cls: 'text-bull'    },
            ].map(({ label, value, cls }) => (
              <div key={label} className="rounded-lg bg-bg-base p-3 text-center">
                <p className="text-[10px] text-muted mb-1">{label}</p>
                <p className={cn('font-mono text-sm font-bold', cls)}>{value}</p>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-2 text-center text-xs sm:grid-cols-4">
            <div>
              <p className="text-muted">Quantity</p>
              <p className="font-mono font-semibold text-primary mt-0.5">{trade.risk.qty} shares</p>
            </div>
            <div>
              <p className="text-muted">Total Cost</p>
              <p className="font-mono font-semibold text-primary mt-0.5">
                ${totalCost.toLocaleString('en-US', { maximumFractionDigits: 0 })}
              </p>
            </div>
            <div>
              <p className="text-muted">R/R Ratio</p>
              <p className="font-mono font-semibold text-brand-cyan mt-0.5">{trade.risk.risk_reward.toFixed(2)}x</p>
            </div>
            <div>
              <p className="text-muted">Dollar Risk</p>
              <p className="font-mono font-semibold text-bear mt-0.5">${trade.risk.dollar_risk.toFixed(0)}</p>
            </div>
          </div>

          <p className="text-center text-[10px] text-muted">
            Quantity will be resized to your account balance at execution.
          </p>

          <div className={cn('flex items-center gap-2 rounded-lg border px-3 py-2 text-xs', regimeColor(trade.regime))}>
            <AlertTriangle className="h-3 w-3 shrink-0" />
            <span>Regime: <strong>{regimeLabel(trade.regime)}</strong></span>
          </div>
        </div>

        {/* Preflight warnings */}
        {!preflight.loading && (marketClosed || insufficientFunds) && (
          <div className="px-6 pb-2 space-y-2">
            {marketClosed && (
              <div className="flex items-start gap-2 rounded-lg border border-caution/30 bg-caution/10 px-3 py-2.5 text-xs text-caution">
                <Clock className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                <div>
                  <p className="font-semibold">Market is closed</p>
                  {preflight.nextOpen && (
                    <p className="text-caution/80 mt-0.5">Opens {formatNextOpen(preflight.nextOpen)}</p>
                  )}
                </div>
              </div>
            )}
            {insufficientFunds && (
              <div className="flex items-start gap-2 rounded-lg border border-bear/30 bg-bear/10 px-3 py-2.5 text-xs text-bear">
                <BanknoteIcon className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                <div>
                  <p className="font-semibold">Insufficient buying power</p>
                  <p className="text-bear/80 mt-0.5">
                    Available ${preflight.buyingPower!.toLocaleString('en-US', { maximumFractionDigits: 0 })} · Trade costs ${tradeCost.toLocaleString('en-US', { maximumFractionDigits: 0 })}
                  </p>
                </div>
              </div>
            )}
          </div>
        )}

        <div className="flex gap-2 border-t border-bg-border px-6 py-4">
          {confirmed ? (
            <div className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-bull/10 py-2.5 text-sm font-semibold text-bull">
              <CheckCircle2 className="h-4 w-4" />
              Trade Submitted!
            </div>
          ) : (
            <>
              <button onClick={onClose} className="btn-ghost flex-1" disabled={loading}>Cancel</button>
              <button
                onClick={handleExecute}
                disabled={loading || preflight.loading || blocked}
                className={cn(
                  'flex-1 rounded-lg py-2.5 text-sm font-semibold transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed',
                  isLong
                    ? 'bg-bull text-bg-base hover:bg-green-400'
                    : 'bg-bear text-white hover:bg-red-400',
                )}
              >
                {preflight.loading ? (
                  <span className="flex items-center justify-center gap-2">
                    <Loader2 className="h-4 w-4 animate-spin" /> Checking...
                  </span>
                ) : loading ? (
                  <span className="flex items-center justify-center gap-2">
                    <Loader2 className="h-4 w-4 animate-spin" /> Executing...
                  </span>
                ) : blocked ? (
                  'Cannot Execute'
                ) : (
                  `Execute ${trade.direction} ${trade.ticker}`
                )}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
