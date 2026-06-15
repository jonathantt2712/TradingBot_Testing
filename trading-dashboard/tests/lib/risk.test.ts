import { describe, it, expect } from 'vitest'
import { sizePosition } from '@/lib/risk'

describe('lib/risk sizePosition', () => {
  it('sizes by risk-per-trade when that is the binding constraint', () => {
    // equity=10000, risk 1% = $100, perShareRisk = 50-48 = 2 -> 50 shares by risk
    // exposure cap: 20% of 10000 / 50 = 40 shares -> exposure is binding
    const qty = sizePosition(10000, 50, 48)
    expect(qty).toBe(40)
  })

  it('sizes by exposure when risk is not binding', () => {
    // equity=100000, risk 1% = $1000, perShareRisk = 10 -> 100 shares by risk
    // exposure cap: 20% of 100000 / 50 = 400 shares -> risk (100) binds
    const qty = sizePosition(100000, 50, 40)
    expect(qty).toBe(100)
  })

  it('returns 0 when equity is zero or negative', () => {
    expect(sizePosition(0, 50, 48)).toBe(0)
    expect(sizePosition(-100, 50, 48)).toBe(0)
  })

  it('returns 0 when entry equals stopLoss (zero per-share risk)', () => {
    expect(sizePosition(10000, 50, 50)).toBe(0)
  })

  it('returns 0 when entry is zero or negative', () => {
    expect(sizePosition(10000, 0, -1)).toBe(0)
  })

  it('floors fractional share counts', () => {
    // equity=1000, risk 1% = $10, perShareRisk = 3 -> 3.33 -> floor to 3
    // exposure cap: 20% of 1000 / 50 = 4 -> risk (3) binds
    const qty = sizePosition(1000, 50, 47)
    expect(qty).toBe(3)
  })
})
