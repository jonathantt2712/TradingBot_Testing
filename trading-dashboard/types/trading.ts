export type Direction = 'LONG' | 'SHORT'
export type Regime    = 'risk_on' | 'neutral' | 'choppy' | 'risk_off'
export type Decision  = 'LONG' | 'SHORT' | 'PASS'

export interface AgentEvaluation {
  role:       string
  score:      number   // 1-100
  confidence: number   // 0-1
  rationale?: string
  reasoning?: Record<string, any>
}

export interface RiskPlan {
  entry:       number
  stop_loss:   number
  take_profit: number
  qty:         number
  risk_reward: number
  dollar_risk: number
}

export interface TradeRecommendation {
  id:                   string
  ticker:               string
  direction:            Direction
  composite_score:      number
  risk:                 RiskPlan
  regime:               Regime
  sector:               string
  hot_sector:           boolean
  evaluations:          AgentEvaluation[]
  timestamp:            string
  expires_at?:          string
  time_window_minutes?: number
  rationale?:           string
  premarket?:           boolean
  gap_pct?:             number
  chg_pct?:             number
  beta?:                number
}

export interface TradeRecord {
  id:         string
  ticker:     string
  direction:  Direction
  entry:      number
  exit:       number | null
  qty:        number
  pnl:        number | null
  pnl_pct:    number | null
  opened_at:  string
  closed_at:  string | null
  duration:   string | null
  status:     'open' | 'closed' | 'cancelled'
  order_id?:  string
}

export interface PnLPoint {
  date:           string
  cumulative_pnl: number
  daily_pnl:      number
  trade_count:    number
  equity?:        number
}

export interface PortfolioStats {
  total_pnl:       number
  today_pnl:       number
  win_rate:        number
  total_trades:    number
  open_positions:  number
  sharpe_ratio:    number
  max_drawdown:    number
  avg_rr:          number
}

export interface RegimeInfo {
  regime:     Regime
  vix_level:  number
  spy_day_chg: number
  qqq_day_chg: number
  rationale:  string
  timestamp:  string
  reasoning?: Record<string, any>
}

export interface SectorStat {
  sector: string
  score:  number
  change: number
  count:  number
}

export interface ScanRejection {
  ticker:      string
  price:       number
  chg_pct:     number
  score:       number | null
  skip_reason: string
  scanned_at:  string
}

export interface ScanResults {
  picked:     TradeRecommendation[]
  rejected:   ScanRejection[]
  scanned_at: string | null
}

export interface ExecuteRequest {
  recommendation_id: string
  ticker:            string
  direction:         Direction
  qty:               number
  entry:             number
  stop_loss:         number
  take_profit:       number
  composite_score?:  number
}

export interface ExecuteResponse {
  success:  boolean
  order_id: string
  qty:      number
  message:  string
}
