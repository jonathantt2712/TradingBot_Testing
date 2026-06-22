import { describe, it, expect } from 'vitest'
import { humanizeRationale } from '@/lib/explainAgent'

// The rationale strings below are the *exact* formats the Python agents emit,
// so these tests double as a contract check between the bot and the dashboard.

describe('humanizeRationale — squeeze', () => {
  // SqueezeAgent emits "short_ratio={:.2%}" → already a percentage string.
  const rationale = 'short_ratio=70.00% | squeeze_long | rel_vol=3.0x | price=up'

  it('does not multiply the already-percent short ratio by 100', () => {
    const out = humanizeRationale('squeeze', rationale)!
    expect(out).toContain('70.0%')
    expect(out).not.toContain('7000')          // the bug it replaces
    expect(out).toMatch(/squeeze setup detected/)
  })

  it('renders short_pressure (underscore) as selling pressure', () => {
    const out = humanizeRationale('squeeze', 'short_ratio=62.00% | short_pressure | rel_vol=1.2x | price=down')!
    expect(out).toContain('62.0%')
    expect(out).toMatch(/short selling pressure/)
  })

  it('falls back gracefully when there is no FINRA data', () => {
    expect(humanizeRationale('squeeze', 'no FINRA short volume data')).toMatch(/No FINRA/i)
  })
})

describe('humanizeRationale — other agents', () => {
  it('explains a technical rationale in plain English', () => {
    const out = humanizeRationale('technical', 'RSI=25.1 MACD_h=-0.08 EMA(↓) px<VWAP day=-7.9% RS=0.92 vol=1.5x')!
    expect(out).toMatch(/oversold/)
    expect(out).toMatch(/bearish momentum/)
    expect(out).toMatch(/down 7.9% today/)
    expect(out.endsWith('.')).toBe(true)
  })

  it('summarises the keyword fundamental fallback', () => {
    const out = humanizeRationale('fundamental', '[keyword] +5/-2 signals')!
    expect(out).toContain('5 bullish')
    expect(out).toContain('2 bearish')
    expect(out).toMatch(/net bullish/)
  })

  it('explains a risk plan and surfaces a veto', () => {
    const out = humanizeRationale('risk', 'R/R=1.20 qty=10 SL=95.00 TP=110.00 VETO')!
    expect(out).toContain('1.20x')
    expect(out).toMatch(/vetoed/)
  })

  it('explains a macro rationale with direction prefix', () => {
    const out = humanizeRationale('macro', 'macro bullish: BTC_7d=+5.0% | QQQ_20d=+3.0% | spread=+2.0%')!
    expect(out).toMatch(/Risk-on environment/)
    expect(out).toContain('BTC up 5.0%')
  })

  it('returns null for empty rationale and passes through unknown roles', () => {
    expect(humanizeRationale('technical', '')).toBeNull()
    expect(humanizeRationale('whatever', 'raw string')).toBe('raw string')
  })
})
