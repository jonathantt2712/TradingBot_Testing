'use client'
import { useEffect, useState } from 'react'
import { ArrowUpRight, ArrowDownLeft, Flame, Zap, TrendingUp, TrendingDown, Clock, RefreshCw, Info } from 'lucide-react'
import { cn, formatPrice, bgColorForScore } from '@/lib/utils'
import type { TradeRecommendation } from '@/types/trading'

interface Props {
  trade:         TradeRecommendation
  onExecute:     (trade: TradeRecommendation) => void
  onInfo:        (trade: TradeRecommendation) => void
  currentPrice?: number
}

const AGENT_COLORS: Record<string, string> = {
  technical:   'bg-brand-cyan',
  fundamental: 'bg-purple-400',
  vision:      'bg-indigo-400',
  risk:        'bg-caution',
  social:      'bg-pink-400',
  liquid:      'bg-teal-400',
}

function useCountdown(expiresAt?: string) {
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null)
  useEffect(() => {
    if (!expiresAt) return
    const tick = () => setSecondsLeft(Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000))
    tick()
    const id = setInterval(tick, 10_000)
    return () => clearInterval(id)
  }, [expiresAt])
  return secondsLeft
}

function CountdownBadge({ secondsLeft }: { secondsLeft: number | null }) {
  if (secondsLeft === null) return null
  const mins    = Math.floor(Math.abs(secondsLeft) / 60)
  const expired = secondsLeft <= 0
  const color   = expired
    ? 'border-muted/30 text-muted bg-bg-hover'
    : secondsLeft < 300
      ? 'border-bear/30 text-bear bg-bear/10'
      : secondsLeft < 1200
        ? 'border-caution/30 text-caution bg-caution/10'
        : 'border-bull/30 text-bull bg-bull/10'
  return (
    <div className={cn('flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-mono font-semibold', color)}>
      {expired ? <RefreshCw className="h-2.5 w-2.5" /> : <Clock className="h-2.5 w-2.5" />}
      {expired ? 'Re-eval...' : `${mins}m`}
    </div>
  )
}

export function TradeCard({ trade, onExecute, onInfo, currentPrice }: Props) {
  const isLong   = trade.direction === 'LONG'
  const dirColor = isLong ? 'text-bull' : 'text-bear'
  const dirBg    = isLong ? 'bg-bull/10 border-bull/25' : 'bg-bear/10 border-bear/25'

  const gapPct = currentPrice
    ? ((currentPrice - trade.risk.entry) / trade.risk.entry) * 100
    : null
  const gapFav = gapPct != null && (isLong ? gapPct >= 0 : gapPct <= 0)

  const totalCost   = trade.risk.qty * trade.risk.entry
  const secondsLeft = useCountdown(trade.expires_at)
  const isExpiring  = secondsLeft !== null && secondsLeft < 300 && secondsLeft > 0
  const isExpired   = secondsLeft !== null && secondsLeft <= 0

  return (
    <div className={cn(
      'card p-5 animate-slide-up transition-all duration-200 hover:border-bg-hover',
      isLong ? 'shadow-glow-bull' : 'shadow-glow-bear',
      isExpiring && 'ring-1 ring-caution/30',
      isExpired  && 'opacity-70',
    )}>
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 mb-4">
        <div className="flex items-center gap-3 min-w-0">
          <div className={cn('flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border', dirBg)}>
            {isLong
              ? <ArrowUpRight className={cn('h-5 w-5', dirColor)} />
              : <ArrowDownLeft className={cn('h-5 w-5', dirColor)} />
            }
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className={cn('ticker-mono text-xl', dirColor)}>{trade.ticker}</span>
              {trade.hot_sector && (
                <span className="flex items-center gap-0.5 rounded-full bg-caution/15 border border-caution/25 px-1.5 py-0.5 text-[10px] font-semibold text-caution">
                  <Flame className="h-2.5 w-2.5" /> HOT
                </span>
              )}
              <button
                onClick={() => onInfo(trade)}
                title="Why this recommendation?"
                className="text-muted hover:text-brand-cyan transition-colors"
              >
                <Info className="h-3.5 w-3.5" />
              </button>
            </div>
            <p className="text-xs text-muted truncate">{trade.sector}</p>
          </div>
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1 ml-auto">
          <div className={cn('badge', bgColorForScore(trade.composite_score))}>
            <Zap className="h-3 w-3" />
            {trade.composite_score.toFixed(0)}
          </div>
          {trade.expires_at && <CountdownBadge secondsLeft={secondsLeft} />}
          {currentPrice != null && (
            <div className={cn(
              'flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-mono font-semibold',
              gapFav ? 'border-bull/30 text-bull bg-bull/10' : 'border-bear/30 text-bear bg-bear/10',
            )}>
              {gapFav ? <TrendingUp className="h-2.5 w-2.5" /> : <TrendingDown className="h-2.5 w-2.5" />}
              {formatPrice(currentPrice)}
              {gapPct != null && (
                <span className="ml-0.5 opacity-70">({gapPct >= 0 ? '+' : ''}{gapPct.toFixed(2)}%)</span>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 mb-4">
        {[
          { label: 'Entry',  value: formatPrice(trade.risk.entry),      color: 'text-primary' },
          { label: 'Stop',   value: formatPrice(trade.risk.stop_loss),  color: 'text-bear'    },
          { label: 'Target', value: formatPrice(trade.risk.take_profit),color: 'text-bull'    },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded-lg bg-bg-base px-3 py-2 text-center">
            <p className="text-[10px] text-muted mb-0.5">{label}</p>
            <p className={cn('font-mono text-sm font-semibold', color)}>{value}</p>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-3 mb-4 text-xs text-muted">
        <span>Qty <span className="text-subtle font-mono">{trade.risk.qty}</span></span>
        <span>R/R <span className="text-brand-cyan font-mono font-semibold">{trade.risk.risk_reward.toFixed(2)}x</span></span>
        <span>Risk <span className="text-bear font-mono">${trade.risk.dollar_risk.toFixed(0)}</span></span>
        <span className="ml-auto font-semibold text-subtle">
          Total <span className="text-primary font-mono">
            ${totalCost >= 1000 ? `${(totalCost / 1000).toFixed(1)}k` : totalCost.toFixed(0)}
          </span>
        </span>
      </div>

      <div className="space-y-1.5 mb-4">
        {(trade.evaluations ?? []).map(ev => (
          <div key={ev.role} className="flex items-center gap-2">
            <span className="w-20 text-[10px] text-muted capitalize">{ev.role}</span>
            <div className="flex-1 score-bar-track">
              <div
                className={cn('h-full rounded-full transition-all duration-700', AGENT_COLORS[ev.role] ?? 'bg-subtle')}
                style={{ width: `${ev.score}%`, opacity: 0.7 + ev.confidence * 0.3 }}
              />
            </div>
            <span className="w-7 text-right text-[10px] font-mono text-subtle">{ev.score}</span>
          </div>
        ))}
      </div>

      <button
        onClick={() => onExecute(trade)}
        className={cn(
          'w-full rounded-lg py-2.5 text-sm font-semibold transition-all duration-200',
          isLong
            ? 'bg-bull/15 border border-bull/30 text-bull hover:bg-bull/25'
            : 'bg-bear/15 border border-bear/30 text-bear hover:bg-bear/25',
        )}
      >
        {isLong ? 'Execute Long' : 'Execute Short'} &middot; {trade.ticker}
      </button>
    </div>
  )
}
