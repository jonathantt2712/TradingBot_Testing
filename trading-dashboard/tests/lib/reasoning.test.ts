import { describe, it, expect } from 'vitest'
import { reasoningToSections } from '@/lib/reasoning'

describe('reasoningToSections', () => {
  it('returns an empty array for null/undefined/non-object input', () => {
    expect(reasoningToSections(null)).toEqual([])
    expect(reasoningToSections(undefined)).toEqual([])
    expect(reasoningToSections('not an object' as any)).toEqual([])
  })

  it('renders technical-agent signals, scalar details, and a note', () => {
    const sections = reasoningToSections({
      signals: [
        { name: 'rsi', display: 'RSI (14)', raw: '28.4', score: 68, weight_pct: 15, direction: 'bullish', note: 'oversold' },
      ],
      price: 887.5,
      vwap: 880.2,
      rsi: 28.4,
      day_change_pct: 1.8,
      flags: { lottery_stock: false, retail_driven: true },
      note: 'Technical note',
    })

    expect(sections[0]).toEqual({
      type: 'signals',
      signals: [
        { name: 'rsi', display: 'RSI (14)', raw: '28.4', score: 68, weight_pct: 15, direction: 'bullish', note: 'oversold' },
      ],
    })

    const details = sections.find(s => s.type === 'grid' && s.label === 'Details')
    expect(details).toBeTruthy()
    expect((details as any).entries).toEqual(
      expect.arrayContaining([
        ['price', '887.5'],
        ['vwap', '880.2'],
        ['rsi', '28.4'],
        ['day_change_pct', '1.8'],
      ]),
    )
    // 'flags' is an object — excluded from the scalar Details grid
    expect((details as any).entries.find((e: string[]) => e[0] === 'flags')).toBeUndefined()

    expect(sections.at(-1)).toEqual({ type: 'text', label: 'Note', text: 'Technical note' })
  })

  it('renders fundamental LLM reasoning as text + details + note', () => {
    const sections = reasoningToSections({
      provider: 'anthropic',
      articles_analyzed: 6,
      headlines_sample: ['NVDA beats Q2 estimates', 'Analysts raise price target'],
      llm_rationale: 'Strong earnings beat supports continued momentum.',
      score: 71,
      confidence: 0.74,
      note: 'Score 1=bearish, 100=bullish',
    })

    expect(sections).toEqual(
      expect.arrayContaining([
        { type: 'list', label: 'Recent headlines', items: ['NVDA beats Q2 estimates', 'Analysts raise price target'] },
        { type: 'text', label: 'LLM analysis', text: 'Strong earnings beat supports continued momentum.' },
        { type: 'text', label: 'Note', text: 'Score 1=bearish, 100=bullish' },
      ]),
    )
    const details = sections.find(s => s.type === 'grid' && s.label === 'Details') as any
    expect(details.entries).toEqual(
      expect.arrayContaining([['provider', 'anthropic'], ['articles_analyzed', '6'], ['score', '71'], ['confidence', '0.74']]),
    )
  })

  it('renders fundamental keyword-fallback reasoning with matched-phrase lists', () => {
    const sections = reasoningToSections({
      provider: 'keyword_fallback',
      articles_analyzed: 4,
      headlines_sample: ['TSLA recall expands to 200k vehicles'],
      bull_signals: 1,
      bear_signals: 3,
      bull_phrases_matched: ['price target raised'],
      bear_phrases_matched: ['recall expands', 'regulatory probe'],
      bull_keywords_matched: ['upgrade'],
      bear_keywords_matched: ['recall', 'probe'],
      note: 'No LLM available',
    })

    expect(sections).toEqual(
      expect.arrayContaining([
        { type: 'list', label: 'Recent headlines', items: ['TSLA recall expands to 200k vehicles'] },
        { type: 'list', label: 'Bullish phrases matched', items: ['price target raised'] },
        { type: 'list', label: 'Bearish phrases matched', items: ['recall expands', 'regulatory probe'] },
        { type: 'list', label: 'Bullish keywords matched', items: ['upgrade'] },
        { type: 'list', label: 'Bearish keywords matched', items: ['recall', 'probe'] },
      ]),
    )
  })

  it('renders vision reasoning with the identified pattern as the text label', () => {
    const sections = reasoningToSections({
      provider: 'anthropic',
      pattern_identified: 'Bull flag',
      analysis: 'Tight consolidation after a strong upward impulse.',
      raw_score: 68,
      note: 'Score 1=bearish setup, 100=bullish setup',
    })

    expect(sections).toEqual(
      expect.arrayContaining([
        { type: 'text', label: 'Pattern: Bull flag', text: 'Tight consolidation after a strong upward impulse.' },
      ]),
    )
    const details = sections.find(s => s.type === 'grid' && s.label === 'Details') as any
    expect(details.entries).toEqual(expect.arrayContaining([['provider', 'anthropic'], ['raw_score', '68']]))
  })

  it('renders liquid-agent signals plus scalar details', () => {
    const sections = reasoningToSections({
      signals: [
        { name: 'rel_vol', display: 'Relative Volume', raw: '2.30x 20-day avg', score: 71, direction: 'bullish', note: 'high volume' },
      ],
      relative_volume: 2.3,
      intraday_direction: 'up',
      note: 'Equity flow note',
    })

    expect(sections[0].type).toBe('signals')
    const details = sections.find(s => s.type === 'grid' && s.label === 'Details') as any
    expect(details.entries).toEqual(expect.arrayContaining([['relative_volume', '2.3'], ['intraday_direction', 'up']]))
  })

  it('renders social-agent reasoning as a Details grid plus note (no signals/lists)', () => {
    const sections = reasoningToSections({
      signals_analyzed: 14,
      trade_signals: 9,
      strategy_signals: 5,
      bull_weight: 8.4,
      bear_weight: 2.1,
      sentiment_ratio: 0.8,
      note: 'Signals sourced from AI4Trade community feed.',
    })

    expect(sections).toEqual([
      {
        type: 'grid',
        label: 'Details',
        entries: [
          ['signals_analyzed', '14'],
          ['trade_signals', '9'],
          ['strategy_signals', '5'],
          ['bull_weight', '8.4'],
          ['bear_weight', '2.1'],
          ['sentiment_ratio', '0.8'],
        ],
      },
      { type: 'text', label: 'Note', text: 'Signals sourced from AI4Trade community feed.' },
    ])
  })

  it('renders a warning section when the risk agent vetoes, plus plan/sizing/thresholds grids', () => {
    const sections = reasoningToSections({
      veto: true,
      veto_reason: 'Position size rounds to zero',
      plan: { entry: 100, stop_loss: 98, take_profit: 106, qty: 0, risk_reward: 3, risk_per_trade_usd: 0 },
      sizing: { account_equity: 10, max_risk_pct: 0.01 },
      thresholds: { min_risk_reward: 1.5, max_open_positions: 5, max_daily_loss_pct: 0.03 },
      note: 'Stop distance = ATR x multiple',
    })

    expect(sections[0]).toEqual({ type: 'warning', text: 'Position size rounds to zero' })
    expect(sections).toEqual(
      expect.arrayContaining([
        { type: 'grid', label: 'Plan', entries: [['entry', '100'], ['stop_loss', '98'], ['take_profit', '106'], ['qty', '0'], ['risk_reward', '3'], ['risk_per_trade_usd', '0']] },
        { type: 'grid', label: 'Sizing', entries: [['account_equity', '10'], ['max_risk_pct', '0.01']] },
        { type: 'grid', label: 'Thresholds', entries: [['min_risk_reward', '1.5'], ['max_open_positions', '5'], ['max_daily_loss_pct', '0.03']] },
      ]),
    )
  })

  it('does not render a warning when the risk agent does not veto', () => {
    const sections = reasoningToSections({
      veto: false,
      veto_reason: null,
      plan: { entry: 100, stop_loss: 98, take_profit: 106, qty: 5, risk_reward: 3, risk_per_trade_usd: 10 },
      sizing: {},
      thresholds: {},
      note: 'note',
    })

    expect(sections.find(s => s.type === 'warning')).toBeUndefined()
  })

  it('renders regime reasoning as Inputs/Rules grids without duplicating regime/rationale', () => {
    const sections = reasoningToSections({
      regime: 'risk_on',
      rationale: 'SPY +0.82%, QQQ +1.14%, VIX 14.2 — bullish',
      inputs: { vix: 14.2, spy_day_chg_pct: 0.82, qqq_day_chg_pct: 1.14 },
      rules: {
        risk_on: 'SPY and QQQ both up > 0.5% intraday and VIX < 25',
        risk_off: 'SPY down > 0.5% intraday or VIX > 35',
      },
    })

    expect(sections).toEqual([
      { type: 'grid', label: 'Inputs', entries: [['vix', '14.2'], ['spy_day_chg_pct', '0.82'], ['qqq_day_chg_pct', '1.14']] },
      { type: 'grid', label: 'Regime rules', entries: [['risk_on', 'SPY and QQQ both up > 0.5% intraday and VIX < 25'], ['risk_off', 'SPY down > 0.5% intraday or VIX > 35']] },
    ])
  })
})
