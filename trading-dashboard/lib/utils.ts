import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatCurrency(n: number, decimals = 2): string {
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `${n < 0 ? '-' : ''}$${(abs / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000)     return `${n < 0 ? '-' : ''}$${(abs / 1_000).toFixed(1)}K`
  return `${n >= 0 ? '+' : ''}$${n.toFixed(decimals)}`
}

export function formatPct(n: number, decimals = 2): string {
  return `${n >= 0 ? '+' : ''}${n.toFixed(decimals)}%`
}

export function formatPrice(n: number): string {
  return `$${n.toFixed(2)}`
}

export function colorForPnl(n: number): string {
  if (n > 0) return 'text-bull'
  if (n < 0) return 'text-bear'
  return 'text-subtle'
}

export function bgColorForScore(score: number): string {
  if (score >= 65) return 'bg-bull/20 text-bull border-bull/30'
  if (score <= 35) return 'bg-bear/20 text-bear border-bear/30'
  return 'bg-caution/20 text-caution border-caution/30'
}

export function regimeLabel(r: string): string {
  return { risk_on: 'RISK ON', neutral: 'NEUTRAL', risk_off: 'RISK OFF' }[r] ?? r.toUpperCase()
}

export function regimeColor(r: string): string {
  return {
    risk_on:  'text-bull bg-bull/10 border-bull/30',
    neutral:  'text-caution bg-caution/10 border-caution/30',
    risk_off: 'text-bear bg-bear/10 border-bear/30',
  }[r] ?? 'text-subtle'
}
