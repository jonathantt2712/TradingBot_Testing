import type {
  TradeRecommendation,
  TradeRecord,
  PnLPoint,
  PortfolioStats,
  RegimeInfo,
  SectorStat,
  ScanResults,
  ExecuteRequest,
  ExecuteResponse,
  LearningData,
  ValidationData,
} from '@/types/trading'

/**
 * Client-side API — calls the Next.js API routes (which proxy to Alpaca/bot).
 * Safe to use in client components; no credentials exposed.
 */
async function clientGet<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: 'no-store' })
  if (!res.ok) throw new Error(`${path} → ${res.status}`)
  return res.json()
}

async function clientPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const err = new Error(data?.message ?? `POST ${path} → ${res.status}`)
    Object.assign(err, { status: res.status })
    throw err
  }
  return data as T
}

export const api = {
  recommendations: (): Promise<TradeRecommendation[]>  => clientGet('/api/bot/recommendations'),
  history:         (): Promise<TradeRecord[]>           => clientGet('/api/bot/history'),
  pnl:             (): Promise<PnLPoint[]>              => clientGet('/api/bot/pnl'),
  stats:           (): Promise<PortfolioStats>          => clientGet('/api/bot/stats'),
  regime:          (): Promise<RegimeInfo>              => clientGet('/api/bot/regime'),
  sectors:         (): Promise<SectorStat[]>            => clientGet('/api/bot/sectors'),
  positions:       ()                                   => clientGet('/api/alpaca/positions'),
  account:         ()                                   => clientGet('/api/alpaca/account'),
  snapshots:       (syms: string[])                     => clientGet(`/api/alpaca/snapshots?symbols=${syms.join(',')}`),
  scanResults:     (): Promise<ScanResults>                        => clientGet('/api/bot/scan-results'),
  learning:        (): Promise<LearningData>                       => clientGet('/api/bot/learning'),
  validation:      (): Promise<ValidationData>                     => clientGet('/api/bot/validation'),
  simulateLearning: (): Promise<LearningData>                      => clientPost('/api/bot/learning/simulate', {}),
  execute:         (req: ExecuteRequest): Promise<ExecuteResponse> => clientPost('/api/bot/execute', req),
}

// ─── Demo fallback data (used when bot API is offline) ──────────────────────

export function demoRecommendations(): TradeRecommendation[] {
  return [
    {
      id: '1', ticker: 'NVDA', direction: 'LONG', composite_score: 74,
      risk: { entry: 887.50, stop_loss: 872.00, take_profit: 927.00, qty: 5, risk_reward: 2.55, dollar_risk: 77.50 },
      regime: 'risk_on', sector: 'Technology', hot_sector: true, timestamp: new Date().toISOString(),
      rationale: 'Breakout above VWAP with rising relative strength vs SPY and 2.1x average volume.',
      evaluations: [
        {
          role: 'technical', score: 78, confidence: 0.82, rationale: 'Price > VWAP, RS=1.18, vol=2.1x [ORB breakout]',
          reasoning: {
            signals: [
              { name: 'vwap', display: 'VWAP Deviation', raw: 'price +0.83% vs VWAP', score: 72, weight_pct: 15, direction: 'bullish', note: 'Price above session VWAP (880.20) by 0.83%' },
              { name: 'rel_strength', display: 'Relative Strength vs SPY', raw: '1.18x', score: 75, weight_pct: 10, direction: 'bullish', note: 'Outperforming SPY by ratio 1.18' },
              { name: 'volume_surge', display: 'Volume Surge', raw: '2.1x 20-day avg', score: 80, weight_pct: 10, direction: 'bullish', note: "Today's projected volume is 2.1x the 20-day average — unusual activity" },
            ],
            price: 887.50, vwap: 880.20, rsi: 61.4, day_change_pct: 1.8,
            flags: { lottery_stock: false, retail_driven: true, retail_surcharge: 5 },
            note: 'Signals weighted by configured agent weights; direction = bullish (>60), bearish (<40), neutral otherwise.',
          },
        },
        {
          role: 'fundamental', score: 71, confidence: 0.74, rationale: '[keyword] +3/-1 signals — positive earnings chatter',
          reasoning: {
            provider: 'keyword_fallback', articles_analyzed: 5,
            headlines_sample: ['NVDA beats Q2 estimates', 'Analysts raise price target to $1000'],
            bull_signals: 3, bear_signals: 1,
            bull_phrases_matched: ['price target raised'], bear_phrases_matched: [],
            bull_keywords_matched: ['beat', 'upgrade', 'growth'], bear_keywords_matched: ['recall'],
            note: 'No LLM available — scoring via keyword matching. Confidence capped at 0.45.',
          },
        },
        {
          role: 'vision', score: 68, confidence: 0.65, rationale: 'Bull flag: continuation pattern forming',
          reasoning: {
            provider: 'anthropic', pattern_identified: 'Bull flag',
            analysis: 'Tight consolidation after a strong upward impulse, with volume drying up — classic continuation setup.',
            raw_score: 68,
            note: 'Score 1=strong bearish chart setup, 50=neutral, 100=strong bullish chart setup',
          },
        },
        {
          role: 'risk', score: 80, confidence: 0.90, rationale: 'R/R 2.55x, within max position size',
          reasoning: {
            veto: false, veto_reason: null,
            plan: { entry: 887.50, stop_loss: 872.00, take_profit: 927.00, qty: 5, risk_reward: 2.55, risk_per_trade_usd: 77.50 },
            sizing: { account_equity: 10000, max_risk_pct: 0.01, risk_usd: 100, max_position_pct: 0.20, atr: 3.2, atr_stop_multiple: 2.0, atr_target_multiple: 3.0 },
            thresholds: { min_risk_reward: 1.5, max_open_positions: 5, max_daily_loss_pct: 0.03 },
            note: 'Stop distance = ATR × stop_multiple. Target capped at session high (LONG) or low (SHORT) to keep R/R variable. Position sized at 1% equity risk per trade, capped at 20% of equity.',
          },
        },
        {
          role: 'social', score: 72, confidence: 0.60, rationale: 'Elevated bullish chatter across 14 community signals',
          reasoning: {
            signals_analyzed: 14, trade_signals: 9, strategy_signals: 5,
            bull_weight: 8.4, bear_weight: 2.1, sentiment_ratio: 0.8,
          },
        },
      ],
    },
    {
      id: '2', ticker: 'TSLA', direction: 'SHORT', composite_score: 28,
      risk: { entry: 182.40, stop_loss: 191.00, take_profit: 163.00, qty: 10, risk_reward: 2.26, dollar_risk: 86.00 },
      regime: 'risk_on', sector: 'Consumer', hot_sector: false, timestamp: new Date().toISOString(),
      rationale: 'Below VWAP with negative relative strength vs SPY; momentum fading on declining volume.',
      evaluations: [
        {
          role: 'technical', score: 25, confidence: 0.78, rationale: 'Price < VWAP, RS=0.84, vol=0.7x — weak momentum',
          reasoning: {
            signals: [
              { name: 'vwap', display: 'VWAP Deviation', raw: 'price -0.62% vs VWAP', score: 28, weight_pct: 15, direction: 'bearish', note: 'Price below session VWAP (183.53) by 0.62%' },
              { name: 'rel_strength', display: 'Relative Strength vs SPY', raw: '0.84x', score: 22, weight_pct: 10, direction: 'bearish', note: 'Underperforming SPY by ratio 0.84' },
              { name: 'volume_surge', display: 'Volume Surge', raw: '0.7x 20-day avg', score: 45, weight_pct: 10, direction: 'neutral', note: "Today's projected volume is 0.7x the 20-day average — normal volume" },
            ],
            price: 182.40, vwap: 183.53, rsi: 38.2, day_change_pct: -1.4,
            flags: { lottery_stock: false, retail_driven: false, retail_surcharge: null },
            note: 'Signals weighted by configured agent weights; direction = bullish (>60), bearish (<40), neutral otherwise.',
          },
        },
        {
          role: 'fundamental', score: 31, confidence: 0.70, rationale: '[keyword] +1/-3 signals — recall/regulatory headlines',
          reasoning: {
            provider: 'keyword_fallback', articles_analyzed: 5,
            headlines_sample: ['TSLA recall expands to 200k vehicles', 'Regulator opens new probe'],
            bull_signals: 1, bear_signals: 3,
            bull_phrases_matched: [], bear_phrases_matched: ['recall expands', 'opens new probe'],
            bull_keywords_matched: ['upgrade'], bear_keywords_matched: ['recall', 'probe', 'decline'],
            note: 'No LLM available — scoring via keyword matching. Confidence capped at 0.45.',
          },
        },
        {
          role: 'vision', score: 27, confidence: 0.62, rationale: 'Descending triangle: bearish continuation',
          reasoning: {
            provider: 'anthropic', pattern_identified: 'Descending triangle',
            analysis: 'Lower highs with flat support — pattern typically resolves to the downside on a volume breakout.',
            raw_score: 27,
            note: 'Score 1=strong bearish chart setup, 50=neutral, 100=strong bullish chart setup',
          },
        },
        {
          role: 'risk', score: 30, confidence: 0.88, rationale: 'R/R 2.26x, position sized to 1% risk',
          reasoning: {
            veto: false, veto_reason: null,
            plan: { entry: 182.40, stop_loss: 191.00, take_profit: 163.00, qty: 10, risk_reward: 2.26, risk_per_trade_usd: 86.00 },
            sizing: { account_equity: 10000, max_risk_pct: 0.01, risk_usd: 100, max_position_pct: 0.20, atr: 1.8, atr_stop_multiple: 2.0, atr_target_multiple: 3.0 },
            thresholds: { min_risk_reward: 1.5, max_open_positions: 5, max_daily_loss_pct: 0.03 },
            note: 'Stop distance = ATR × stop_multiple. Target capped at session high (LONG) or low (SHORT) to keep R/R variable. Position sized at 1% equity risk per trade, capped at 20% of equity.',
          },
        },
        {
          role: 'social', score: 28, confidence: 0.55, rationale: 'Bearish-leaning community sentiment',
          reasoning: {
            signals_analyzed: 10, trade_signals: 6, strategy_signals: 4,
            bull_weight: 1.8, bear_weight: 6.2, sentiment_ratio: 0.225,
          },
        },
      ],
    },
    {
      id: '3', ticker: 'MSFT', direction: 'LONG', composite_score: 68,
      risk: { entry: 428.00, stop_loss: 420.50, take_profit: 447.00, qty: 3, risk_reward: 2.53, dollar_risk: 22.50 },
      regime: 'risk_on', sector: 'Technology', hot_sector: true, timestamp: new Date().toISOString(),
      rationale: 'Steady uptrend above VWAP with broad sector strength in Technology.',
      evaluations: [
        {
          role: 'technical', score: 72, confidence: 0.80, rationale: 'Price > VWAP, RS=1.05, vol=1.3x',
          reasoning: {
            signals: [
              { name: 'vwap', display: 'VWAP Deviation', raw: 'price +0.41% vs VWAP', score: 65, weight_pct: 15, direction: 'bullish', note: 'Price above session VWAP (426.25) by 0.41%' },
              { name: 'rel_strength', display: 'Relative Strength vs SPY', raw: '1.05x', score: 60, weight_pct: 10, direction: 'bullish', note: 'Outperforming SPY by ratio 1.05' },
              { name: 'volume_surge', display: 'Volume Surge', raw: '1.3x 20-day avg', score: 58, weight_pct: 10, direction: 'neutral', note: "Today's projected volume is 1.3x the 20-day average — normal volume" },
            ],
            price: 428.00, vwap: 426.25, rsi: 58.1, day_change_pct: 0.7,
            flags: { lottery_stock: false, retail_driven: false, retail_surcharge: null },
            note: 'Signals weighted by configured agent weights; direction = bullish (>60), bearish (<40), neutral otherwise.',
          },
        },
        {
          role: 'fundamental', score: 66, confidence: 0.75, rationale: '[keyword] +2/-0 signals — cloud growth headlines',
          reasoning: {
            provider: 'keyword_fallback', articles_analyzed: 3,
            headlines_sample: ['Microsoft cloud revenue grows 22%', 'Azure adds new enterprise customers'],
            bull_signals: 2, bear_signals: 0,
            bull_phrases_matched: ['revenue grows'], bear_phrases_matched: [],
            bull_keywords_matched: ['growth', 'adds'], bear_keywords_matched: [],
            note: 'No LLM available — scoring via keyword matching. Confidence capped at 0.45.',
          },
        },
        {
          role: 'vision', score: 65, confidence: 0.68, rationale: 'Ascending channel, holding key support',
          reasoning: {
            provider: 'anthropic', pattern_identified: 'Ascending channel',
            analysis: 'Price holding the lower trendline of a well-defined ascending channel, with momentum intact.',
            raw_score: 65,
            note: 'Score 1=strong bearish chart setup, 50=neutral, 100=strong bullish chart setup',
          },
        },
        {
          role: 'risk', score: 75, confidence: 0.91, rationale: 'R/R 2.53x, within max position size',
          reasoning: {
            veto: false, veto_reason: null,
            plan: { entry: 428.00, stop_loss: 420.50, take_profit: 447.00, qty: 3, risk_reward: 2.53, risk_per_trade_usd: 22.50 },
            sizing: { account_equity: 10000, max_risk_pct: 0.01, risk_usd: 100, max_position_pct: 0.20, atr: 2.5, atr_stop_multiple: 2.0, atr_target_multiple: 3.0 },
            thresholds: { min_risk_reward: 1.5, max_open_positions: 5, max_daily_loss_pct: 0.03 },
            note: 'Stop distance = ATR × stop_multiple. Target capped at session high (LONG) or low (SHORT) to keep R/R variable. Position sized at 1% equity risk per trade, capped at 20% of equity.',
          },
        },
        {
          role: 'social', score: 60, confidence: 0.58, rationale: 'Mildly positive community sentiment',
          reasoning: {
            signals_analyzed: 8, trade_signals: 5, strategy_signals: 3,
            bull_weight: 4.8, bear_weight: 3.2, sentiment_ratio: 0.6,
          },
        },
      ],
    },
  ]
}

export function demoStats(): PortfolioStats {
  return {
    total_pnl: 4_820.50,
    today_pnl: 312.75,
    win_rate: 64.2,
    total_trades: 47,
    open_positions: 2,
    sharpe_ratio: 1.84,
    max_drawdown: -8.3,
    avg_rr: 2.4,
  }
}

export function demoPnL(): PnLPoint[] {
  const points: PnLPoint[] = []
  let cum = 0
  for (let i = 30; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i)
    const daily = (Math.random() - 0.35) * 400
    cum += daily
    points.push({
      date:           d.toISOString().slice(0, 10),
      cumulative_pnl: +cum.toFixed(2),
      daily_pnl:      +daily.toFixed(2),
      trade_count:    Math.floor(Math.random() * 4) + 1,
    })
  }
  return points
}

export function demoRegime(): RegimeInfo {
  return {
    regime: 'risk_on',
    vix_level: 14.2,
    spy_day_chg: 0.82,
    qqq_day_chg: 1.14,
    rationale: 'VIX below 20, SPY and QQQ both above VWAP with positive intraday momentum.',
    timestamp: new Date().toISOString(),
    reasoning: {
      regime: 'risk_on',
      rationale: 'SPY +0.82%, QQQ +1.14%, VIX 14.2 — bullish',
      inputs: {
        vix: 14.2,
        vix_label: 'VIX',
        spy_day_chg_pct: 0.82,
        qqq_day_chg_pct: 1.14,
      },
      rules: {
        risk_on:  'SPY and QQQ both up > 0.5% intraday and VIX < 25',
        risk_off: 'SPY down > 0.5% intraday or VIX > 35',
        choppy:   'SPY and QQQ both within ±0.3% intraday',
        neutral:  'All other conditions',
      },
    },
  }
}

export function demoSectors(): SectorStat[] {
  return [
    { sector: 'Technology',  score: 72, change: 1.4,   count: 8 },
    { sector: 'Consumer',    score: 58, change: 0.3,   count: 4 },
    { sector: 'Financials',  score: 61, change: 0.7,   count: 5 },
    { sector: 'Healthcare',  score: 48, change: -0.2,  count: 4 },
    { sector: 'Energy',      score: 44, change: -0.5,  count: 3 },
    { sector: 'Communication', score: 65, change: 0.9, count: 3 },
  ]
}
