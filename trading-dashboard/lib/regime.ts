import { getSnapshots, type AlpacaCreds } from '@/lib/alpaca'
import type { RegimeInfo, Regime } from '@/types/trading'

async function fetchVix(): Promise<number> {
  try {
    const res = await fetch(
      'https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=5d',
      { headers: { 'User-Agent': 'Mozilla/5.0' }, cache: 'no-store' }
    )
    if (!res.ok) return 0
    const data = await res.json()
    const closes: (number | null)[] = data?.chart?.result?.[0]?.indicators?.quote?.[0]?.close ?? []
    for (let i = closes.length - 1; i >= 0; i--) {
      if (closes[i] != null) return +closes[i]!.toFixed(1)
    }
  } catch { /* fall through */ }
  return 0
}

function snapChg(snap: { dailyBar?: { c?: number }; prevDailyBar?: { c?: number } } | undefined): number {
  const curr = snap?.dailyBar?.c ?? 0
  const prev = snap?.prevDailyBar?.c ?? 0
  if (!curr || !prev) return 0
  return +((curr - prev) / prev * 100).toFixed(2)
}

/** Compute current market regime directly from Alpaca snapshots + Yahoo VIX.
 *  Never depends on the bot — always fresh. */
export async function computeRegime(creds: AlpacaCreds): Promise<RegimeInfo> {
  const [snaps, vix] = await Promise.all([
    getSnapshots(creds, ['SPY', 'QQQ', 'VIXY']),
    fetchVix(),
  ])

  const spyChg  = snapChg(snaps['SPY'])
  const qqqChg  = snapChg(snaps['QQQ'])
  const vixProxy = +(snaps['VIXY']?.latestTrade?.p ?? snaps['VIXY']?.dailyBar?.c ?? 0).toFixed(1)
  const vixLevel = vix > 0 ? vix : vixProxy > 0 ? vixProxy : 15
  const vixLabel = vix > 0 ? 'VIX' : 'VIX-proxy'

  let regime: Regime
  let rationale: string
  if (spyChg > 0.5 && qqqChg > 0.5 && vixLevel < 25) {
    regime    = 'risk_on'
    rationale = `SPY +${spyChg.toFixed(2)}%, QQQ +${qqqChg.toFixed(2)}%, ${vixLabel} ${vixLevel} — bullish`
  } else if (spyChg < -0.5 || vixLevel > 35) {
    regime    = 'risk_off'
    rationale = `SPY ${spyChg.toFixed(2)}%, ${vixLabel} ${vixLevel} — bearish`
  } else if (Math.abs(spyChg) < 0.3 && Math.abs(qqqChg) < 0.3) {
    regime    = 'choppy'
    rationale = `SPY ${spyChg.toFixed(2)}%, QQQ ${qqqChg.toFixed(2)}% — low momentum`
  } else {
    regime    = 'neutral'
    rationale = `SPY ${spyChg.toFixed(2)}%, QQQ ${qqqChg.toFixed(2)}%`
  }

  return {
    regime,
    vix_level:   vixLevel,
    spy_day_chg: spyChg,
    qqq_day_chg: qqqChg,
    rationale,
    timestamp:   new Date().toISOString(),
  }
}
