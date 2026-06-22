import { describe, it, expect } from 'vitest'
import {
  formatCurrency, formatPct, formatPrice, colorForPnl,
  bgColorForScore, regimeLabel, regimeColor,
} from '@/lib/utils'

describe('formatCurrency', () => {
  it('abbreviates millions and thousands with a sign', () => {
    expect(formatCurrency(2_500_000)).toBe('$2.5M')
    expect(formatCurrency(-2_500_000)).toBe('-$2.5M')
    expect(formatCurrency(12_300)).toBe('$12.3K')
    expect(formatCurrency(-12_300)).toBe('-$12.3K')
  })

  it('shows small amounts with a leading + for non-negatives', () => {
    expect(formatCurrency(42)).toBe('+$42.00')
    expect(formatCurrency(0)).toBe('+$0.00')
  })
})

describe('formatPct / formatPrice', () => {
  it('formats percentages with explicit sign', () => {
    expect(formatPct(3.5)).toBe('+3.50%')
    expect(formatPct(-1.2)).toBe('-1.20%')
  })
  it('formats price to two decimals', () => {
    expect(formatPrice(887.5)).toBe('$887.50')
  })
})

describe('colorForPnl', () => {
  it('maps sign to bull/bear/subtle', () => {
    expect(colorForPnl(10)).toBe('text-bull')
    expect(colorForPnl(-10)).toBe('text-bear')
    expect(colorForPnl(0)).toBe('text-subtle')
  })
})

describe('bgColorForScore', () => {
  it('uses bull/caution/bear bands at 65 and 35', () => {
    expect(bgColorForScore(70)).toContain('bull')
    expect(bgColorForScore(50)).toContain('caution')
    expect(bgColorForScore(30)).toContain('bear')
    // boundaries are inclusive
    expect(bgColorForScore(65)).toContain('bull')
    expect(bgColorForScore(35)).toContain('bear')
  })
})

describe('regime helpers', () => {
  it('labels known regimes and upper-cases unknowns', () => {
    expect(regimeLabel('risk_on')).toBe('RISK ON')
    expect(regimeLabel('risk_off')).toBe('RISK OFF')
    expect(regimeLabel('mystery')).toBe('MYSTERY')
  })
  it('returns a class string for each regime, with a fallback', () => {
    expect(regimeColor('risk_on')).toContain('bull')
    expect(regimeColor('risk_off')).toContain('bear')
    expect(regimeColor('unknown')).toBe('text-subtle')
  })
})
