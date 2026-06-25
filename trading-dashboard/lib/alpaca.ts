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

// Market clock

export interface AlpacaClock {
  timestamp:  string
  is_open:    boolean
  next_open:  string
  next_close: string
}

export function getClock(creds: AlpacaCreds): Promise<AlpacaClock> {
  return alpacaGet<AlpacaClock>(brokerBase(creds), '/v2/clock', creds)
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
  const safeStatus = (['open', 'closed', 'all'] as const).includes(status as any) ? status : 'closed'
  const safeLimit  = Math.min(Math.max(1, Math.floor(Number(limit))), 500)
  return alpacaGet(brokerBase(creds), `/v2/orders?status=${safeStatus}&limit=${safeLimit}&direction=desc`, creds)
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

// Account fill activities

export interface AlpacaFill {
  id:               string
  activity_type:    string
  symbol:           string
  side:             'buy' | 'sell'
  qty:              string
  price:            string
  transaction_time: string
  type:             string
}

export async function getFills(creds: AlpacaCreds, pageSize = 200): Promise<AlpacaFill[]> {
  return alpacaGet<AlpacaFill[]>(
    brokerBase(creds),
    `/v2/account/activities?activity_type=FILL&page_size=${pageSize}&direction=asc`,
    creds,
  )
}

/**
 * Merge two trade lists, preferring `primary` records when both share the same
 * ticker + date key. Returns a single list sorted newest-first.
 */
export function mergeTrades(
  primary:   import('@/types/trading').TradeRecord[],
  secondary: import('@/types/trading').TradeRecord[],
): import('@/types/trading').TradeRecord[] {
  const keys = new Set(primary.map(t => `${t.ticker}-${t.opened_at?.slice(0, 10)}`))
  return [
    ...primary,
    ...secondary.filter(t => !keys.has(`${t.ticker}-${t.opened_at?.slice(0, 10)}`)),
  ].sort((a, b) => (b.opened_at ?? '').localeCompare(a.opened_at ?? ''))
}

/**
 * Convert Alpaca closed orders into completed round-trip TradeRecord objects using FIFO.
 * Orders must be sorted oldest-first. Each time a position returns to flat a trade is emitted.
 */
export function tradesFromOrders(orders: AlpacaOrder[]): import('@/types/trading').TradeRecord[] {
  type Pos = { qty: number; avgCost: number; openedAt: string; runningPnl: number }
  const pos: Record<string, Pos> = {}
  const trades: import('@/types/trading').TradeRecord[] = []
  const EPS = 0.0001

  // Process oldest-first for correct FIFO matching
  const sorted = [...orders]
    .filter(o => parseFloat(o.filled_qty ?? '0') > 0 && o.filled_avg_price)
    .sort((a, b) => (a.filled_at ?? a.created_at).localeCompare(b.filled_at ?? b.created_at))

  for (const o of sorted) {
    const qty   = parseFloat(o.filled_qty!)
    const price = parseFloat(o.filled_avg_price!)
    const sym   = o.symbol
    const ts    = o.filled_at ?? o.created_at
    if (!pos[sym]) pos[sym] = { qty: 0, avgCost: 0, openedAt: ts, runningPnl: 0 }
    const p = pos[sym]

    if (o.side === 'buy') {
      if (p.qty < 0) {
        const cover = Math.min(qty, -p.qty)
        p.runningPnl += (p.avgCost - price) * cover
        p.qty        += cover
        if (Math.abs(p.qty) < EPS) {
          trades.push({ id: o.id, ticker: sym, direction: 'SHORT',
            entry: +p.avgCost.toFixed(4), exit: +price.toFixed(4), qty: cover,
            pnl: +p.runningPnl.toFixed(2),
            pnl_pct: +(p.runningPnl / (p.avgCost * cover) * 100).toFixed(2),
            opened_at: p.openedAt, closed_at: ts, duration: null, status: 'closed' })
          p.qty = 0; p.avgCost = 0; p.runningPnl = 0
        }
        const rem = qty - cover
        if (rem > EPS) { p.qty = rem; p.avgCost = price; p.openedAt = ts; p.runningPnl = 0 }
      } else {
        p.avgCost = p.qty === 0 ? price : (p.avgCost * p.qty + price * qty) / (p.qty + qty)
        if (p.qty === 0) p.openedAt = ts
        p.qty += qty
      }
    } else {
      if (p.qty > 0) {
        const sell = Math.min(qty, p.qty)
        p.runningPnl += (price - p.avgCost) * sell
        p.qty        -= sell
        if (Math.abs(p.qty) < EPS) {
          trades.push({ id: o.id, ticker: sym, direction: 'LONG',
            entry: +p.avgCost.toFixed(4), exit: +price.toFixed(4), qty: sell,
            pnl: +p.runningPnl.toFixed(2),
            pnl_pct: +(p.runningPnl / (p.avgCost * sell) * 100).toFixed(2),
            opened_at: p.openedAt, closed_at: ts, duration: null, status: 'closed' })
          p.qty = 0; p.avgCost = 0; p.runningPnl = 0
        }
        const rem = qty - sell
        if (rem > EPS) { p.qty = -rem; p.avgCost = price; p.openedAt = ts; p.runningPnl = 0 }
      } else {
        p.avgCost = p.qty === 0 ? price : (p.avgCost * (-p.qty) + price * qty) / (-p.qty + qty)
        if (p.qty === 0) p.openedAt = ts
        p.qty -= qty
      }
    }
  }

  return trades.sort((a, b) => (b.opened_at ?? '').localeCompare(a.opened_at ?? ''))
}

/**
 * Convert raw Alpaca fill activities into completed round-trip TradeRecord objects.
 * Uses the same FIFO algorithm as winRateFromFills but emits full trade details.
 */
export function tradesFromFills(fills: AlpacaFill[]): import('@/types/trading').TradeRecord[] {
  type Pos = { qty: number; avgCost: number; openedAt: string; runningPnl: number }
  const pos: Record<string, Pos> = {}
  const trades: import('@/types/trading').TradeRecord[] = []
  const EPS = 0.0001

  for (const f of fills) {
    const qty   = parseFloat(f.qty)
    const price = parseFloat(f.price)
    const sym   = f.symbol
    const ts    = f.transaction_time
    if (!pos[sym]) pos[sym] = { qty: 0, avgCost: 0, openedAt: ts, runningPnl: 0 }
    const p = pos[sym]

    if (f.side === 'buy') {
      if (p.qty < 0) {
        const cover = Math.min(qty, -p.qty)
        p.runningPnl += (p.avgCost - price) * cover
        p.qty        += cover
        if (Math.abs(p.qty) < EPS) {
          const totalQty = cover
          trades.push({ id: `${sym}-${p.openedAt}`, ticker: sym, direction: 'SHORT',
            entry: +p.avgCost.toFixed(4), exit: +price.toFixed(4), qty: totalQty,
            pnl: +p.runningPnl.toFixed(2),
            pnl_pct: +(p.runningPnl / (p.avgCost * totalQty) * 100).toFixed(2),
            opened_at: p.openedAt, closed_at: ts, duration: null, status: 'closed' })
          p.qty = 0; p.avgCost = 0; p.runningPnl = 0
        }
        const rem = qty - cover
        if (rem > EPS) { p.qty = rem; p.avgCost = price; p.openedAt = ts; p.runningPnl = 0 }
      } else {
        p.avgCost = p.qty === 0 ? price : (p.avgCost * p.qty + price * qty) / (p.qty + qty)
        if (p.qty === 0) p.openedAt = ts
        p.qty += qty
      }
    } else {
      if (p.qty > 0) {
        const sell = Math.min(qty, p.qty)
        p.runningPnl += (price - p.avgCost) * sell
        p.qty        -= sell
        if (Math.abs(p.qty) < EPS) {
          const totalQty = sell
          trades.push({ id: `${sym}-${p.openedAt}`, ticker: sym, direction: 'LONG',
            entry: +p.avgCost.toFixed(4), exit: +price.toFixed(4), qty: totalQty,
            pnl: +p.runningPnl.toFixed(2),
            pnl_pct: +(p.runningPnl / (p.avgCost * totalQty) * 100).toFixed(2),
            opened_at: p.openedAt, closed_at: ts, duration: null, status: 'closed' })
          p.qty = 0; p.avgCost = 0; p.runningPnl = 0
        }
        const rem = qty - sell
        if (rem > EPS) { p.qty = -rem; p.avgCost = price; p.openedAt = ts; p.runningPnl = 0 }
      } else {
        p.avgCost = p.qty === 0 ? price : (p.avgCost * (-p.qty) + price * qty) / (-p.qty + qty)
        if (p.qty === 0) p.openedAt = ts
        p.qty -= qty
      }
    }
  }

  return trades.sort((a, b) => (b.opened_at ?? '').localeCompare(a.opened_at ?? ''))
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
