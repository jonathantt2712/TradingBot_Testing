/**
 * Server-side Alpaca REST client.
 * Never import this from client components -- keys stay on the server.
 */

const KEY_ID  = process.env.ALPACA_KEY_ID     ?? ''
const SECRET  = process.env.ALPACA_SECRET      ?? ''
const PAPER   = process.env.ALPACA_PAPER !== 'false'

const BROKER_BASE = PAPER
  ? 'https://paper-api.alpaca.markets'
  : 'https://api.alpaca.markets'

const DATA_BASE = 'https://data.alpaca.markets'

const HEADERS = {
  'APCA-API-KEY-ID':     KEY_ID,
  'APCA-API-SECRET-KEY': SECRET,
  'Content-Type':        'application/json',
}

async function alpacaGet<T>(base: string, path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${base}${path}`, {
    headers: HEADERS,
    next: { revalidate: 10 },
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

export function getAccount(): Promise<AlpacaAccount> {
  return alpacaGet(BROKER_BASE, '/v2/account')
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

export function getPositions(): Promise<AlpacaPosition[]> {
  return alpacaGet(BROKER_BASE, '/v2/positions', { cache: 'no-store' })
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

export function getOrders(status = 'closed', limit = 50): Promise<AlpacaOrder[]> {
  return alpacaGet(BROKER_BASE, `/v2/orders?status=${status}&limit=${limit}&direction=desc`)
}

// Latest quote

export interface AlpacaQuote {
  symbol: string
  quote:  { ap: number; bp: number; as: number; bs: number; t: string }
}

export async function getLatestQuote(symbol: string): Promise<AlpacaQuote> {
  const data = await alpacaGet<{ quotes: Record<string, any> }>(
    DATA_BASE, `/v2/stocks/${symbol}/quotes/latest`
  )
  return { symbol, quote: data.quotes?.[symbol] }
}

// Latest bar

export interface AlpacaBar {
  symbol: string
  bar:    { o: number; h: number; l: number; c: number; v: number; t: string }
}

export async function getLatestBar(symbol: string): Promise<AlpacaBar> {
  const data = await alpacaGet<{ bars: Record<string, any> }>(
    DATA_BASE, `/v2/stocks/${symbol}/bars/latest`
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

export async function getSnapshots(symbols: string[]): Promise<Record<string, AlpacaSnapshot>> {
  const syms = symbols.join(',')
  const data = await alpacaGet<{ snapshots: Record<string, AlpacaSnapshot> }>(
    DATA_BASE, `/v2/stocks/snapshots?symbols=${encodeURIComponent(syms)}`
  )
  return data.snapshots ?? (data as any)
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

export async function submitBracketOrder(req: BracketOrderRequest): Promise<AlpacaOrderResponse> {
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

  const res = await fetch(`${BROKER_BASE}/v2/orders`, {
    method:  'POST',
    headers: HEADERS,
    body:    JSON.stringify(body),
    cache:   'no-store',
  })

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`Alpaca submit order -> ${res.status}: ${text}`)
  }
  return res.json()
}

// Close a position

export async function closePosition(symbol: string): Promise<AlpacaOrderResponse> {
  const res = await fetch(`${BROKER_BASE}/v2/positions/${symbol}`, {
    method:  'DELETE',
    headers: HEADERS,
    cache:   'no-store',
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`Alpaca close position ${symbol} -> ${res.status}: ${text}`)
  }
  return res.json()
}
