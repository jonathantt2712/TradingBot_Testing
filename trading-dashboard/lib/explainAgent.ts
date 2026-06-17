/**
 * Translate the terse, technical rationale strings produced by each agent
 * (e.g. "RSI=25.1 MACD_h=-0.0819 EMA(↓) px<VWAP day=-7.9% RS=0.92 vol=1.5x")
 * into clear, plain-English sentences for the info modal.
 */

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

function humanizeTechnical(r: string): string {
  const parts: string[] = []

  const rsi = r.match(/RSI=(-?[\d.]+)/)
  if (rsi) {
    const v = parseFloat(rsi[1])
    if (v <= 30)      parts.push(`RSI is ${v} (oversold)`)
    else if (v >= 70) parts.push(`RSI is ${v} (overbought)`)
    else              parts.push(`RSI is ${v} (neutral)`)
  }

  const macd = r.match(/MACD_h=(-?[\d.]+)/)
  if (macd) {
    const v = parseFloat(macd[1])
    parts.push(v >= 0
      ? `MACD histogram is positive (${v}), suggesting building bullish momentum`
      : `MACD histogram is negative (${v}), suggesting building bearish momentum`)
  }

  const ema = r.match(/EMA\(([↑↓])\)/)
  if (ema) {
    parts.push(ema[1] === '↑'
      ? 'the short-term trend (EMA) is above the long-term trend, an uptrend'
      : 'the short-term trend (EMA) is below the long-term trend, a downtrend')
  }

  const vwap = r.match(/px([><])VWAP/)
  if (vwap) {
    parts.push(vwap[1] === '>'
      ? 'price is trading above the session VWAP, buyers in control'
      : 'price is trading below the session VWAP, sellers in control')
  }

  const day = r.match(/day=([+-][\d.]+)%/)
  if (day) {
    const v = parseFloat(day[1])
    parts.push(`the stock is ${v >= 0 ? 'up' : 'down'} ${Math.abs(v)}% today`)
  }

  const rs = r.match(/RS=([\d.]+)/)
  if (rs) {
    const v = parseFloat(rs[1])
    parts.push(v >= 1
      ? `it's outperforming SPY (relative strength ${v})`
      : `it's underperforming SPY (relative strength ${v})`)
  }

  const vol = r.match(/vol=([\d.]+)x/)
  if (vol) {
    const v = parseFloat(vol[1])
    parts.push(`trading volume is ${v}x the average${v >= 1.5 ? ' (elevated interest)' : ''}`)
  }

  const orb = r.match(/ORB(↑BRK|↓BRK|=RNG)/)
  if (orb) {
    if (orb[1] === '↑BRK')      parts.push('it broke out above its opening range')
    else if (orb[1] === '↓BRK') parts.push('it broke down below its opening range')
    else                        parts.push('it is still trading inside its opening range')
  }

  if (/\[LOTTERY/.test(r))      parts.push('the score was pulled toward neutral for erratic, lottery-ticket-like price action')
  if (/RETAIL-DRIVEN/.test(r))  parts.push('elevated retail trading activity raised the bar for this signal')

  if (!parts.length) return r
  return cap(parts.join('; ')) + '.'
}

function humanizeFundamental(r: string): string {
  if (/no news/i.test(r)) return 'No recent news was found for this stock.'

  const kw = r.match(/\[keyword\]\s*\+(\d+)\/-(\d+)\s*signals/)
  if (kw) {
    const bull = +kw[1], bear = +kw[2]
    const lean = bull > bear ? 'net bullish' : bull < bear ? 'net bearish' : 'neutral'
    return `A keyword scan of recent news found ${bull} bullish vs ${bear} bearish mentions — ${lean}.`
  }

  const llm = r.match(/^\[(\w+)\]\s*(.*)$/)
  if (llm && llm[2]) return cap(llm[2])

  return r
}

function humanizeVision(r: string): string {
  if (/no vision api key/i.test(r)) return 'Chart-pattern analysis is not enabled (no vision API key configured).'
  if (/no chart image/i.test(r))    return 'No chart image was available to analyze.'
  if (/vision error/i.test(r))      return 'Chart-pattern analysis failed and defaulted to neutral.'

  const m = r.match(/^\[(\w+)\]\s*([^:]*):\s*(.*)$/)
  if (m) {
    const [, , pattern, reason] = m
    return `Detected chart pattern: ${pattern.trim()}${reason ? ` — ${reason}` : ''}.`
  }

  return r
}

function humanizeRisk(r: string): string {
  if (/cannot build/i.test(r)) return 'Could not build a valid position-sizing / stop-loss plan for this stock.'

  const m = r.match(/R\/R=([\d.]+)\s+qty=([\d.]+)\s+SL=([\d.]+)\s+TP=([\d.]+)/)
  if (m) {
    const [, rr, qty, sl, tp] = m
    let s = `Risk/reward is ${rr}x with ${qty} shares — stop-loss at $${sl}, take-profit at $${tp}.`
    if (/VETO/.test(r)) s += ' This trade was vetoed for not meeting the minimum risk/reward requirement.'
    return s
  }

  return r
}

function humanizeSocial(r: string): string {
  if (/no community signals/i.test(r)) return 'No community discussion was found for this stock.'

  const none = r.match(/no directional signals \((\d+) signals parsed\)/)
  if (none) return `Scanned ${none[1]} community posts but found no clear bullish or bearish lean.`

  const m = r.match(/bull_w=([\d.]+)\s+bear_w=([\d.]+)\s+\((\d+) trades?, (\d+) strategies?\)/)
  if (m) {
    const [, bullW, bearW, trades, strategies] = m
    const bw = parseFloat(bullW), brw = parseFloat(bearW)
    const lean = bw > brw ? 'bullish' : bw < brw ? 'bearish' : 'mixed'
    return `Community sentiment leans ${lean} — based on ${trades} trade mentions and ${strategies} strategy discussions (bullish weight ${bullW} vs bearish ${bearW}).`
  }

  return r
}

const LIQUID_LABELS: Record<string, string> = {
  rel_vol:   'relative volume',
  vwap_dev:  'price vs VWAP',
  mom_accel: 'momentum acceleration',
  spread:    'spread / liquidity quality',
}

function humanizeLiquid(r: string): string {
  if (/insufficient bars/i.test(r))     return 'Not enough price history to analyze order flow.'
  if (/all sub-signals failed/i.test(r)) return 'Liquidity and order-flow signals could not be computed.'

  const out: string[] = []
  for (const part of r.split('|')) {
    const m = part.trim().match(/^(\w+)=(-?[\d.]+)$/)
    if (!m) continue
    const [, key, valStr] = m
    const val   = parseFloat(valStr)
    const label = LIQUID_LABELS[key] ?? key
    const lean  = val >= 60 ? 'bullish' : val <= 40 ? 'bearish' : 'neutral'
    out.push(`${label} ${lean} (${val.toFixed(0)}/100)`)
  }

  if (!out.length) return r
  return `Order-flow read: ${out.join(', ')}.`
}

function humanizeInsider(r: string): string {
  if (/no congressional trades/i.test(r))        return 'No congressional trading disclosures found in the last 30 days.'
  if (/no valid congress trades/i.test(r))        return 'No valid disclosures found for this ticker in the last 30 days.'
  if (/fetch failed/i.test(r))                    return 'House Stock Watcher data fetch failed — check network.'
  if (/mixed.*buyers.*sellers/i.test(r))          return r
  const m = r.match(/^(bullish|bearish) past (\d+)d \((\d+) disclosures?\)/)
  if (m) {
    const [, dir, days, n] = m
    return `${n} congressional disclosure${parseInt(n) > 1 ? 's' : ''} are ${dir} over the past ${days} days.`
  }
  return r
}

function humanizeSqueeze(r: string): string {
  if (/no FINRA short volume data/i.test(r))      return 'No FINRA RegSHO short volume data found for today.'
  if (/ticker not found/i.test(r))                return 'Ticker not in FINRA short volume report today (OTC/non-exchange).'
  const ratio = r.match(/short_ratio=([\d.]+)/)
  if (ratio) {
    const v = parseFloat(ratio[1])
    const pct = (v * 100).toFixed(1)
    if (/squeeze/i.test(r))          return `Short ratio is ${pct}% with price moving up — squeeze setup detected.`
    if (/short pressure/i.test(r))   return `Short ratio is ${pct}% with price falling — heavy short selling pressure.`
    if (/heavy shorted/i.test(r))    return `Short ratio is ${pct}% — heavily shorted but no clear directional catalyst.`
    return `Short ratio is ${pct}%.`
  }
  return r
}

/** Returns a plain-English explanation for an agent's rationale, or null if there's nothing to translate. */
export function humanizeRationale(role: string, rationale?: string): string | null {
  if (!rationale) return null
  switch (role) {
    case 'technical':   return humanizeTechnical(rationale)
    case 'fundamental': return humanizeFundamental(rationale)
    case 'vision':      return humanizeVision(rationale)
    case 'risk':        return humanizeRisk(rationale)
    case 'social':      return humanizeSocial(rationale)
    case 'liquid':      return humanizeLiquid(rationale)
    case 'insider':     return humanizeInsider(rationale)
    case 'squeeze':     return humanizeSqueeze(rationale)
    default:            return rationale
  }
}
