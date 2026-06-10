import type {
  TradeRecommendation,
  TradeRecord,
  PnLPoint,
  PortfolioStats,
  RegimeInfo,
  SectorStat,
  ExecuteRequest,
  ExecuteResponse,
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
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`)
  return res.json()
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
        { role: 'technical',    score: 78, confidence: 0.82, rationale: 'Price > VWAP, RS=1.18, vol=2.1x [ORB breakout]' },
        { role: 'fundamental',  score: 71, confidence: 0.74, rationale: '[keyword] +3/-1 signals — positive earnings chatter' },
        { role: 'vision',       score: 68, confidence: 0.65, rationale: 'Bull flag: continuation pattern forming' },
        { role: 'risk',         score: 80, confidence: 0.90, rationale: 'R/R 2.55x, within max position size' },
        { role: 'social',       score: 72, confidence: 0.60, rationale: 'Elevated bullish chatter across 14 community signals' },
      ],
    },
    {
      id: '2', ticker: 'TSLA', direction: 'SHORT', composite_score: 28,
      risk: { entry: 182.40, stop_loss: 191.00, take_profit: 163.00, qty: 10, risk_reward: 2.26, dollar_risk: 86.00 },
      regime: 'risk_on', sector: 'Consumer', hot_sector: false, timestamp: new Date().toISOString(),
      rationale: 'Below VWAP with negative relative strength vs SPY; momentum fading on declining volume.',
      evaluations: [
        { role: 'technical',    score: 25, confidence: 0.78, rationale: 'Price < VWAP, RS=0.84, vol=0.7x — weak momentum' },
        { role: 'fundamental',  score: 31, confidence: 0.70, rationale: '[keyword] +1/-3 signals — recall/regulatory headlines' },
        { role: 'vision',       score: 27, confidence: 0.62, rationale: 'Descending triangle: bearish continuation' },
        { role: 'risk',         score: 30, confidence: 0.88, rationale: 'R/R 2.26x, position sized to 1% risk' },
        { role: 'social',       score: 28, confidence: 0.55, rationale: 'Bearish-leaning community sentiment' },
      ],
    },
    {
      id: '3', ticker: 'MSFT', direction: 'LONG', composite_score: 68,
      risk: { entry: 428.00, stop_loss: 420.50, take_profit: 447.00, qty: 3, risk_reward: 2.53, dollar_risk: 22.50 },
      regime: 'risk_on', sector: 'Technology', hot_sector: true, timestamp: new Date().toISOString(),
      rationale: 'Steady uptrend above VWAP with broad sector strength in Technology.',
      evaluations: [
        { role: 'technical',    score: 72, confidence: 0.80, rationale: 'Price > VWAP, RS=1.05, vol=1.3x' },
        { role: 'fundamental',  score: 66, confidence: 0.75, rationale: '[keyword] +2/-0 signals — cloud growth headlines' },
        { role: 'vision',       score: 65, confidence: 0.68, rationale: 'Ascending channel, holding key support' },
        { role: 'risk',         score: 75, confidence: 0.91, rationale: 'R/R 2.53x, within max position size' },
        { role: 'social',       score: 60, confidence: 0.58, rationale: 'Mildly positive community sentiment' },
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

export function demoHistory(): TradeRecord[] {
  const tickers = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'GOOGL', 'AMD', 'META']
  return Array.from({ length: 20 }, (_, i) => {
    const dir = Math.random() > 0.5 ? 'LONG' : 'SHORT'
    const entry  = 100 + Math.random() * 800
    const pnlPct = (Math.random() - 0.4) * 8
    const exit   = dir === 'LONG' ? entry * (1 + pnlPct / 100) : entry * (1 - pnlPct / 100)
    const qty    = Math.floor(Math.random() * 20) + 1
    const pnl    = (exit - entry) * qty * (dir === 'LONG' ? 1 : -1)
    const d      = new Date(); d.setDate(d.getDate() - i)
    return {
      id:        `t${i}`,
      ticker:    tickers[i % tickers.length],
      direction: dir as 'LONG' | 'SHORT',
      entry:     +entry.toFixed(2),
      exit:      +exit.toFixed(2),
      qty,
      pnl:       +pnl.toFixed(2),
      pnl_pct:   +pnlPct.toFixed(2),
      opened_at: d.toISOString(),
      closed_at: new Date(d.getTime() + Math.random() * 3_600_000).toISOString(),
      duration:  `${Math.floor(Math.random() * 120) + 5}m`,
      status:    'closed',
    } as TradeRecord
  })
}

export function demoRegime(): RegimeInfo {
  return {
    regime: 'risk_on',
    vix_level: 14.2,
    spy_day_chg: 0.82,
    qqq_day_chg: 1.14,
    rationale: 'VIX below 20, SPY and QQQ both above VWAP with positive intraday momentum.',
    timestamp: new Date().toISOString(),
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
