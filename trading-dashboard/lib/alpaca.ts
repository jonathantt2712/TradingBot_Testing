/**
 * Server-side Alpaca REST client.
 * Never import this from client components -- keys stay on the server.
 */

export interface AlpacaCreds {
  keyId:  string
  secret: string
  paper:  boolean
}

const DATA_BASE = 'https://data.alpaca.markets'

function brokerBase(creds: AlpacaCreds): string {
  return creds.paper
    ? 'https://paper-api.alpaca.markets'
    : 'https://api.alpaca.markets'
}

function headers(creds: AlpacaCreds) {
  return {
    'APCA-API-KEY-ID':     creds.keyId,
    'APCA-API-SECRET-KEY': creds.secret,
    'Content-Type':        'application/json',
  }
}

async function alpacaGet<T>(base: string, path: string, creds: AlpacaCreds, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${base}${path}`, {
    headers: headers(creds),
    cache: 'no-store',
    ...opts,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`Alpaca ${path} -> ${res.status}: ${text}`)
  }
  return res.json()
}

// Account

export interface AlpacaAccount {
  id:                  string
  status:              string
  currency:            string
  buying_power:        string
  cash:                string
  portfolio_value:     string
  equity:              string
  last_equity:         string
  unrealized_pl:       string
  unrealized_plpc:     string
  realized_pl:         string
  daytrade_count:      number
  pattern_day_trader:  boolean
}

export function getAccount(creds: AlpacaCreds): Promise<AlpacaAccount> {
  return alpacaGet(brokerBase(creds), '/v2/account', creds)
}

// Positions

export interface AlpacaPosition {
  symbol:           string
  qty:              string
  side:             string
  avg_entry_price:  string
  current_price:    string
  market_value:     string
  unrealized_pl:    string
  unrealized_plpc:  string
  change_today:     string
}

export function getPositions(creds: AlpacaCreds): Promise<AlpacaPosition[]> {
  return alpacaGet(brokerBase(creds), '/v2/positions', creds, { cache: 'no-store' })
}

// Orders

export interface AlpacaOrder {
  id:               string
  symbol:           string
  side:             string
  qty:              string
  filled_qty:       string
  filled_avg_price: string | null
  status:           string
  created_at:       string
  filled_at:        string | null
  type:             string
}

export function getOrders(creds: AlpacaCreds, status = 'closed', limit = 50): Promise<AlpacaOrder[]> {
  return alpacaGet(brokerBase(creds), `/v2/orders?status=${status}&limit=${limit}&direction=desc`, creds)
}

// Latest quote

export interface AlpacaQuote {
  symbol: string
  quote:  { ap: number; bp: number; as: number; bs: number; t: string }
}

export async function getLatestQuote(creds: AlpacaCreds, symbol: string): Promise<AlpacaQuote> {
  const data = await alpacaGet<{ quotes: Record<string, any> }>(
    DATA_BASE, `/v2/stocks/${symbol}/quotes/latest`, creds
  )
  return { symbol, quote: data.quotes?.[symbol] }
}

// Latest bar

export interface AlpacaBar {
  symbol: string
  bar:    { o: number; h: number; l: number; c: number; v: number; t: string }
}

export async function getLatestBar(creds: AlpacaCreds, symbol: string): Promise<AlpacaBar> {
  const data = await alpacaGet<{ bars: Record<string, any> }>(
    DATA_BASE, `/v2/stocks/${symbol}/bars/latest`, creds
  )
  return { symbol, bar: data.bars?.[symbol] }
}

// Multi-ticker snapshot

export interface AlpacaSnapshot {
  symbol:      string
  latestTrade: { p: number; s: number; t: string }
  latestQuote: { ap: number; bp: number }
  dailyBar:    { o: number; h: number; l: number; c: number; v: number }
  prevDailyBar:{ o: number; h: number; l: number; c: number; v: number }
}

export async function getSnapshots(creds: AlpacaCreds, symbols: string[]): Promise<Record<string, AlpacaSnapshot>> {
  const syms = symbols.join(',')
  const data = await alpacaGet<{ snapshots: Record<string, AlpacaSnapshot> }>(
    DATA_BASE, `/v2/stocks/snapshots?symbols=${encodeURIComponent(syms)}`, creds
  )
  return data.snapshots ?? (data as any)
}

// Bars (multi-symbol)

export interface AlpacaBarsResponse {
  bars: Record<string, Array<{ o: number; h: number; l: number; c: number; v: number; t: string }>>
}

export async function getBars(creds: AlpacaCreds, params: URLSearchParams): Promise<AlpacaBarsResponse> {
  return alpacaGet<AlpacaBarsResponse>(DATA_BASE, `/v2/stocks/bars?${params}`, creds, { cache: 'no-store' })
}

// Order submission

export interface BracketOrderRequest {
  symbol:      string
  side:        'buy' | 'sell'
  qty:         number
  stop_loss:   number
  take_profit: number
}

export interface AlpacaOrderResponse {
  id:              string
  client_order_id: string
  symbol:          string
  side:            string
  qty:             string
  status:          string
  created_at:      string
}

export async function submitBracketOrder(creds: AlpacaCreds, req: BracketOrderRequest): Promise<AlpacaOrderResponse> {
  const body = {
    symbol:        req.symbol,
    qty:           String(req.qty),
    side:          req.side,
    type:          'market',
    time_in_force: 'day',
    order_class:   'bracket',
    stop_loss:     { stop_price:  req.stop_loss.toFixed(2) },
    take_profit:   { limit_price: req.take_profit.toFixed(2) },
  }

  const res = await fetch(`${brokerBase(creds)}/v2/orders`, {
    method:  'POST',
    headers: headers(creds),
    body:    JSON.stringify(body),
    cache:   'no-store',
  })

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`Alpaca submit order -> ${res.status}: ${text}`)
  }
  return res.json()
}

// Portfolio history (equity curve)

export interface PortfolioHistory {
  timestamp:       number[]
  equity:          number[]
  profit_loss:     number[]
  profit_loss_pct: number[]
  base_value:      number
}

export async function getPortfolioHistory(
  creds: AlpacaCreds,
  period   = '1M',
  timeframe = '1D',
): Promise<PortfolioHistory> {
  return alpacaGet<PortfolioHistory>(
    brokerBase(creds),
    `/v2/account/portfolio/history?period=${period}&timeframe=${timeframe}&intraday_reporting=market_hours`,
    creds,
  )
}

// Close a position

export async function closePosition(creds: AlpacaCreds, symbol: string): Promise<AlpacaOrderResponse> {
  const res = await fetch(`${brokerBase(creds)}/v2/positions/${symbol}`, {
    method:  'DELETE',
    headers: headers(creds),
    cache:   'no-store',
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`Alpaca close position ${symbol} -> ${res.status}: ${text}`)
  }
  return res.json()
}
