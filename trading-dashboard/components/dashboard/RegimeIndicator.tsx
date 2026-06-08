'use client'
import { Wifi, AlertTriangle, ShieldAlert } from 'lucide-react'
import { cn, regimeLabel, regimeColor } from '@/lib/utils'
import type { RegimeInfo } from '@/types/trading'

interface Props { regime: RegimeInfo }

export function RegimeIndicator({ regime }: Props) {
  const Icon = {
    risk_on:  Wifi,
    neutral:  AlertTriangle,
    risk_off: ShieldAlert,
  }[regime.regime] ?? Wifi

  const color = regimeColor(regime.regime)

  return (
    <div className="card p-4">
      <p className="stat-label mb-3">Market Regime</p>
      <div className="flex items-center gap-3 mb-3">
        <div className={cn('flex h-10 w-10 items-center justify-center rounded-lg border', color)}>
          <Icon className="h-5 w-5" />
        </div>
        <div>
          <p className={cn('text-lg font-bold leading-tight', color.split(' ')[0])}>
            {regimeLabel(regime.regime)}
          </p>
          <p className="text-xs text-muted">Updated just now</p>
        </div>
      </div>

      <div className="space-y-1.5 text-xs">
        <div className="flex justify-between">
          <span className="text-muted">VIX</span>
          <span className={cn('font-mono font-medium', regime.vix_level < 20 ? 'text-bull' : regime.vix_level < 30 ? 'text-caution' : 'text-bear')}>
            {regime.vix_level.toFixed(1)}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted">SPY</span>
          <span className={cn('font-mono font-medium', regime.spy_day_chg >= 0 ? 'text-bull' : 'text-bear')}>
            {regime.spy_day_chg >= 0 ? '+' : ''}{regime.spy_day_chg.toFixed(2)}%
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted">QQQ</span>
          <span className={cn('font-mono font-medium', regime.qqq_day_chg >= 0 ? 'text-bull' : 'text-bear')}>
            {regime.qqq_day_chg >= 0 ? '+' : ''}{regime.qqq_day_chg.toFixed(2)}%
          </span>
        </div>
      </div>

      <div className="mt-3 rounded-md bg-bg-base px-3 py-2 text-[10px] text-muted leading-relaxed">
        {regime.rationale}
      </div>
    </div>
  )
}
