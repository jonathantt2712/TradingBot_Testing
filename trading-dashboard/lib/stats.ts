import type { PnLPoint } from '@/types/trading'

/** Compute annualised Sharpe from an equity curve (preferred) or daily P&L series. */
export function computeSharpe(pnl: PnLPoint[]): number | null {
  const equities = pnl.map(p => p.equity).filter((e): e is number => e != null && e > 0)
  let vals: number[]

  if (equities.length >= 3) {
    vals = []
    for (let i = 1; i < equities.length; i++) {
      vals.push((equities[i] - equities[i - 1]) / equities[i - 1])
    }
  } else {
    vals = pnl.map(p => p.daily_pnl).filter(v => isFinite(v) && v !== 0)
  }

  if (vals.length < 2) return null
  const mean     = vals.reduce((s, v) => s + v, 0) / vals.length
  const variance = vals.reduce((s, v) => s + (v - mean) ** 2, 0) / (vals.length - 1)
  const std      = Math.sqrt(variance)
  if (std === 0) return null
  return +(mean / std * Math.sqrt(252)).toFixed(2)
}

/** Compute max drawdown as a percentage from an equity curve. */
export function computeMaxDD(pnl: PnLPoint[]): number | null {
  const equities = pnl.map(p => p.equity).filter((e): e is number => e != null && e > 0)
  if (equities.length < 2) return null
  let peak = equities[0]
  let maxDD = 0
  for (const e of equities) {
    if (e > peak) peak = e
    const dd = (e - peak) / peak * 100
    if (dd < maxDD) maxDD = dd
  }
  return maxDD < 0 ? +maxDD.toFixed(2) : null
}
