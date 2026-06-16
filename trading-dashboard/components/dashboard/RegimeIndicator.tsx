'use client'
import { useEffect, useState } from 'react'
import { Wifi, AlertTriangle, ShieldAlert, Info, X, Activity, Check, XCircle } from 'lucide-react'
import { cn, regimeLabel, regimeColor } from '@/lib/utils'
import type { RegimeInfo } from '@/types/trading'

interface Props { regime: RegimeInfo }

interface ScanStats {
  market_open?:     boolean | null
  scans_today?:     number
  tickers_scanned?: number
  recs_generated?:  number
  scan_errors?:     number
  last_scan_at?:    string | null
}

export function RegimeIndicator({ regime }: Props) {
  const [showInfo, setShowInfo]   = useState(false)
  const [scanStats, setScanStats] = useState<ScanStats | null>(null)

  useEffect(() => {
    if (!showInfo) return
    let cancelled = false
    fetch('/api/bot/scan-stats', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (!cancelled) setScanStats(d) })
      .catch(() => { if (!cancelled) setScanStats(null) })
    return () => { cancelled = true }
  }, [showInfo])

  const Icon = {
    risk_on:  Wifi,
    neutral:  AlertTriangle,
    risk_off: ShieldAlert,
    choppy:   AlertTriangle,
  }[regime.regime] ?? Wifi

  const color = regimeColor(regime.regime)

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3">
        <p className="stat-label mb-0">Market Regime</p>
        <button
          onClick={() => setShowInfo(true)}
          title="Why is today like this?"
          className="text-muted hover:text-brand-cyan transition-colors"
        >
          <Info className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="flex items-center gap-3 mb-3">
        <div className={cn('flex h-10 w-10 items-center justify-center rounded-lg border', color)}>
          <Icon className="h-5 w-5" />
        </div>
        <div>
          <p className={cn('text-lg font-bold leading-tight', color.split(' ')[0])}>
            {regimeLabel(regime.regime)}
          </p>
          <p className="text-xs text-muted">
            {regime?.timestamp
              ? (() => {
                  const mins = Math.floor((Date.now() - new Date(regime.timestamp).getTime()) / 60000)
                  return mins < 1 ? 'Updated just now' : `Updated ${mins}m ago`
                })()
              : 'No data'}
          </p>
        </div>
      </div>

      <div className="space-y-1.5 text-xs">
        <div className="flex justify-between">
          <span className="text-muted">VIX</span>
          <span className={cn('font-mono font-medium', regime.vix_level < 20 ? 'text-bull' : regime.vix_level < 30 ? 'text-caution' : 'text-bear')}>
            {regime.vix_level.toFixed(1)}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted">SPY</span>
          <span className={cn('font-mono font-medium', regime.spy_day_chg >= 0 ? 'text-bull' : 'text-bear')}>
            {regime.spy_day_chg >= 0 ? '+' : ''}{regime.spy_day_chg.toFixed(2)}%
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted">QQQ</span>
          <span className={cn('font-mono font-medium', regime.qqq_day_chg >= 0 ? 'text-bull' : 'text-bear')}>
            {regime.qqq_day_chg >= 0 ? '+' : ''}{regime.qqq_day_chg.toFixed(2)}%
          </span>
        </div>
      </div>

      <div className="mt-3 rounded-md bg-bg-base px-3 py-2 text-[10px] text-muted leading-relaxed">
        {regime.rationale}
      </div>

      {showInfo && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: 'rgba(2,6,23,0.85)', backdropFilter: 'blur(8px)' }}
          onClick={e => { if (e.target === e.currentTarget) setShowInfo(false) }}
        >
          <div className="w-full max-w-lg rounded-2xl border border-bg-border bg-bg-card shadow-2xl animate-slide-up">
            <div className="flex items-center justify-between border-b border-bg-border px-6 py-4">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-cyan/15">
                  <Activity className="h-5 w-5 text-brand-cyan" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold text-primary">
                    Why today is <span className={color.split(' ')[0]}>{regimeLabel(regime.regime)}</span>
                  </h2>
                  <p className="text-xs text-muted">
                    {regime.timestamp ? `As of ${new Date(regime.timestamp).toLocaleTimeString()}` : 'Latest scan'}
                  </p>
                </div>
              </div>
              <button onClick={() => setShowInfo(false)} className="text-muted hover:text-primary transition-colors">
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="px-6 py-4 space-y-4 max-h-[70vh] overflow-y-auto text-sm text-subtle">
              <p>
                Once per scan cycle, the bot pulls fresh SPY, QQQ and a VIX proxy (VIXY) and re-runs the checks
                below. The result decides today&apos;s regime label and feeds the rationale shown on the dashboard:
              </p>
              <div className="rounded-lg border border-bg-border bg-bg-base px-4 py-3 text-xs font-mono text-subtle">
                &quot;{regime.rationale}&quot;
              </div>

              <div className="space-y-2">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-muted">Today&apos;s readings vs. thresholds</p>

                <div className="rounded-lg border border-bg-border bg-bg-base px-4 py-3 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-primary">VIX proxy (VIXY &times; 10)</span>
                    <span className="font-mono text-sm text-subtle">{regime.vix_level.toFixed(1)}</span>
                  </div>
                  <p className="text-xs text-muted">
                    {regime.vix_level > 35
                      ? 'Above 35 → high fear, this alone triggers RISK OFF.'
                      : regime.vix_level < 25
                        ? 'Below 25 → calm enough to allow RISK ON (still needs SPY & QQQ to confirm).'
                        : 'Between 25 and 35 → elevated but not extreme; doesn’t trigger RISK OFF on its own.'}
                  </p>
                </div>

                <div className="rounded-lg border border-bg-border bg-bg-base px-4 py-3 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-primary">SPY daily change</span>
                    <span className={cn('font-mono text-sm', regime.spy_day_chg >= 0 ? 'text-bull' : 'text-bear')}>
                      {regime.spy_day_chg >= 0 ? '+' : ''}{regime.spy_day_chg.toFixed(2)}%
                    </span>
                  </div>
                  <p className="text-xs text-muted">
                    {regime.spy_day_chg > 0.5
                      ? 'Above +0.5% → bullish, supports RISK ON (if QQQ and VIX agree).'
                      : regime.spy_day_chg < -0.5
                        ? 'Below -0.5% → sharp sell-off, this alone triggers RISK OFF.'
                        : Math.abs(regime.spy_day_chg) < 0.3
                          ? 'Within ±0.3% → flat, contributes to a CHOPPY read.'
                          : 'Between ±0.3% and ±0.5% → some movement, but not enough to confirm a directional regime.'}
                  </p>
                </div>

                <div className="rounded-lg border border-bg-border bg-bg-base px-4 py-3 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-primary">QQQ daily change</span>
                    <span className={cn('font-mono text-sm', regime.qqq_day_chg >= 0 ? 'text-bull' : 'text-bear')}>
                      {regime.qqq_day_chg >= 0 ? '+' : ''}{regime.qqq_day_chg.toFixed(2)}%
                    </span>
                  </div>
                  <p className="text-xs text-muted">
                    {regime.qqq_day_chg > 0.5
                      ? 'Above +0.5% → bullish, supports RISK ON (if SPY and VIX agree).'
                      : Math.abs(regime.qqq_day_chg) < 0.3
                        ? 'Within ±0.3% → flat, contributes to a CHOPPY read.'
                        : 'Outside the flat band but not strongly bullish — adds to a mixed/NEUTRAL picture.'}
                  </p>
                </div>
              </div>

              <div className="space-y-2">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-muted">Decision path (checked in order)</p>
                {([
                  ['risk_on',  'RISK ON',  'SPY > +0.5% AND QQQ > +0.5% AND VIX proxy < 25'],
                  ['risk_off', 'RISK OFF', 'SPY < −0.5% OR VIX proxy > 35'],
                  ['choppy',   'CHOPPY',   '|SPY| < 0.3% AND |QQQ| < 0.3%'],
                  ['neutral',  'NEUTRAL',  'none of the above (mixed signals)'],
                ] as const).map(([key, label, desc]) => {
                  const isActive = regime.regime === key || (regime.regime !== 'risk_on' && regime.regime !== 'risk_off' && regime.regime !== 'choppy' && key === 'neutral')
                  return (
                    <div
                      key={key}
                      className={cn(
                        'flex items-start gap-2 rounded-lg border px-3 py-2 text-xs',
                        isActive ? 'border-brand-cyan/40 bg-brand-cyan/5' : 'border-bg-border bg-bg-base opacity-60',
                      )}
                    >
                      {isActive
                        ? <Check className="h-3.5 w-3.5 mt-0.5 shrink-0 text-brand-cyan" />
                        : <XCircle className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted" />
                      }
                      <div>
                        <span className={cn('font-semibold', isActive ? 'text-primary' : 'text-subtle')}>{label}</span>
                        <span className="text-muted"> — {desc}</span>
                      </div>
                    </div>
                  )
                })}
              </div>

              <div className="space-y-2">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-muted">What the bot is doing today</p>
                {scanStats ? (
                  <div className="rounded-lg border border-bg-border bg-bg-base px-4 py-3 grid grid-cols-2 gap-y-1.5 text-xs">
                    <span className="text-muted">Market status</span>
                    <span className="text-right font-mono text-subtle">
                      {scanStats.market_open == null ? 'unknown' : scanStats.market_open ? 'open' : 'closed (scanning anyway)'}
                    </span>
                    <span className="text-muted">Scans run today</span>
                    <span className="text-right font-mono text-subtle">{scanStats.scans_today ?? '—'}</span>
                    <span className="text-muted">Tickers scanned</span>
                    <span className="text-right font-mono text-subtle">{scanStats.tickers_scanned ?? '—'}</span>
                    <span className="text-muted">Recommendations generated</span>
                    <span className="text-right font-mono text-subtle">{scanStats.recs_generated ?? '—'}</span>
                    <span className="text-muted">Scan errors</span>
                    <span className={cn('text-right font-mono', (scanStats.scan_errors ?? 0) > 0 ? 'text-bear' : 'text-subtle')}>
                      {scanStats.scan_errors ?? '—'}
                    </span>
                    {scanStats.last_scan_at && (
                      <>
                        <span className="text-muted">Last scan</span>
                        <span className="text-right font-mono text-subtle">
                          {new Date(scanStats.last_scan_at + 'Z').toLocaleTimeString()}
                        </span>
                      </>
                    )}
                  </div>
                ) : (
                  <div className="rounded-lg border border-bg-border bg-bg-base px-4 py-3 text-xs text-muted">
                    Scan activity unavailable — bot server may be offline.
                  </div>
                )}
              </div>

              <div className="rounded-lg border border-bg-border bg-bg-base px-4 py-3">
                <p className="text-sm font-semibold text-primary mb-1">How this connects to recommendations</p>
                <p className="text-xs text-subtle">
                  Each scanned ticker is independently scored by the Technical, Fundamental, Vision (chart), Risk,
                  Social Sentiment and Liquidity Flow agents — see the <span className="text-brand-cyan">ⓘ</span> on
                  any trade card for that breakdown. The regime above is the session-level backdrop those scores are
                  generated against today.
                </p>
              </div>
            </div>

            <div className="border-t border-bg-border px-6 py-4">
              <button onClick={() => setShowInfo(false)} className="btn-ghost w-full">Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
