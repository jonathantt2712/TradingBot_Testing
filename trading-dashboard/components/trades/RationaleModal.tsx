'use client'
import { X, ArrowUpRight, ArrowDownLeft, Lightbulb, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { cn, formatPrice, regimeLabel, regimeColor, bgColorForScore } from '@/lib/utils'
import { humanizeRationale } from '@/lib/explainAgent'
import type { TradeRecommendation, AgentEvaluation } from '@/types/trading'

interface Props {
  trade:   TradeRecommendation | null
  onClose: () => void
}

const AGENT_ORDER = ['technical', 'fundamental', 'vision', 'risk', 'social', 'liquid', 'squeeze'] as const

const AGENT_LABELS: Record<string, string> = {
  technical:   'Technical',
  fundamental: 'Fundamental',
  vision:      'Vision (Chart)',
  risk:        'Risk',
  social:      'Social Sentiment',
  liquid:      'Liquidity Flow',
  squeeze:     'Short Squeeze',
}

const AGENT_BLURBS: Record<string, string> = {
  technical:   'Price action, VWAP, relative strength & volume',
  fundamental: 'News & earnings keyword signals',
  vision:      'Chart pattern recognition',
  risk:        'Position sizing, stop placement & R/R viability',
  social:      'Community / social sentiment chatter',
  liquid:      'Order flow & liquidity dynamics',
  squeeze:     'FINRA daily short volume ratio — detects squeeze setups and short-covering signals',
}

/**
 * Detect when an agent didn't actually run (returns a neutral 50 because it
 * lacks a key, a chart, or feed data) so we can label it "Not configured"
 * instead of showing a misleading score. Matches the rationale strings the
 * Python agents emit in their degraded paths.
 */
function notConfiguredReason(ev: AgentEvaluation): string | null {
  const r = (ev.rationale || '').toLowerCase()
  if (r.includes('no vision api key'))     return 'No vision API key set on the bot server (Railway)'
  if (r.includes('no chart image'))        return 'No chart image available for this evaluation'
  if (r.includes('vision error'))          return 'Vision API call failed — check the key/quota'
  if (r.includes('no community signals'))  return 'No AI4Trade community feed data (or creds not set)'
  if (r.includes('no directional signals')) return 'Community feed returned no directional signal'
  return null
}

/** bullish (score >= 55), bearish (<= 45), neutral otherwise */
function lean(score: number): 'bull' | 'bear' | 'neutral' {
  if (score >= 55) return 'bull'
  if (score <= 45) return 'bear'
  return 'neutral'
}

function LeanIcon({ score }: { score: number }) {
  const l = lean(score)
  if (l === 'bull')    return <TrendingUp className="h-3.5 w-3.5 text-bull" />
  if (l === 'bear')    return <TrendingDown className="h-3.5 w-3.5 text-bear" />
  return <Minus className="h-3.5 w-3.5 text-muted" />
}

function buildVerdict(trade: TradeRecommendation): string {
  const isLong = trade.direction === 'LONG'
  const evals  = trade.evaluations ?? []
  const agree  = evals.filter(e => isLong ? e.score >= 55 : e.score <= 45)
  const oppose = evals.filter(e => isLong ? e.score <= 45 : e.score >= 55)
  const avgConf = evals.length ? Math.round(evals.reduce((s, e) => s + e.confidence, 0) / evals.length * 100) : null

  const reward = Math.abs(trade.risk.take_profit - trade.risk.entry) * trade.risk.qty

  const sentences: string[] = []

  if (evals.length) {
    let agreementSentence = `${agree.length} of ${evals.length} agent${evals.length === 1 ? '' : 's'} lean ${isLong ? 'bullish' : 'bearish'}, agreeing with this ${trade.direction} call`
    if (oppose.length) agreementSentence += `, while ${oppose.length} ${oppose.length === 1 ? 'disagrees' : 'disagree'}`
    sentences.push(agreementSentence + '.')
  }

  sentences.push(
    `The composite score of ${trade.composite_score.toFixed(0)}/100`
    + (avgConf != null ? ` (average agent confidence ${avgConf}%)` : '')
    + ` was generated under a ${regimeLabel(trade.regime)} market regime.`
  )

  sentences.push(
    `Risking $${trade.risk.dollar_risk.toFixed(0)} (entry ${formatPrice(trade.risk.entry)} → stop ${formatPrice(trade.risk.stop_loss)}) `
    + `for a potential gain of $${reward.toFixed(0)} (target ${formatPrice(trade.risk.take_profit)}) — `
    + `a ${trade.risk.risk_reward.toFixed(2)}x risk/reward on ${trade.risk.qty} share${trade.risk.qty === 1 ? '' : 's'}.`
  )

  return sentences.join(' ')
}

export function RationaleModal({ trade, onClose }: Props) {
  if (!trade) return null

  const isLong   = trade.direction === 'LONG'
  const dirColor = isLong ? 'text-bull' : 'text-bear'
  const dirBg    = isLong ? 'border-bull/30' : 'border-bear/30'

  const evalMap = new Map<string, AgentEvaluation>((trade.evaluations ?? []).map(e => [e.role, e]))

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(2,6,23,0.85)', backdropFilter: 'blur(8px)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className={cn('w-full max-w-lg rounded-2xl border bg-bg-card shadow-2xl animate-slide-up', dirBg)}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-bg-border px-6 py-4">
          <div className="flex items-center gap-3">
            <div className={cn('flex h-9 w-9 items-center justify-center rounded-lg', isLong ? 'bg-bull/15' : 'bg-bear/15')}>
              {isLong ? <ArrowUpRight className="h-5 w-5 text-bull" /> : <ArrowDownLeft className="h-5 w-5 text-bear" />}
            </div>
            <div>
              <h2 className="text-sm font-semibold text-primary">
                Why <span className={dirColor}>{trade.ticker}</span> · {trade.direction}
              </h2>
              <p className="text-xs text-muted">Full agent breakdown behind this recommendation</p>
            </div>
          </div>
          <button onClick={onClose} className="text-muted hover:text-primary transition-colors">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-6 py-4 space-y-4 max-h-[70vh] overflow-y-auto">
          <div className="flex items-center justify-between rounded-xl bg-bg-base px-4 py-3">
            <div className={cn('flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold', bgColorForScore(trade.composite_score))}>
              <Lightbulb className="h-3.5 w-3.5" />
              Composite score: {trade.composite_score.toFixed(0)}
            </div>
            <div className={cn('rounded-full border px-3 py-1 text-xs font-semibold', regimeColor(trade.regime))}>
              {regimeLabel(trade.regime)}
            </div>
          </div>

          {/* Verdict */}
          <div className={cn('rounded-lg border px-4 py-3', isLong ? 'border-bull/30 bg-bull/5' : 'border-bear/30 bg-bear/5')}>
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted mb-1">
              Why {isLong ? 'buy' : 'short'} {trade.ticker}
            </p>
            <p className="text-sm text-subtle leading-relaxed">{buildVerdict(trade)}</p>
          </div>

          {trade.rationale && (
            <div className="rounded-lg border border-bg-border bg-bg-base px-4 py-3">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-muted mb-1">Signal summary</p>
              <p className="text-sm text-subtle">{humanizeRationale('technical', trade.rationale) ?? trade.rationale}</p>
            </div>
          )}

          {/* Per-agent breakdown — only agents with actual evaluations */}
          <div className="space-y-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted">What each agent said</p>
            {AGENT_ORDER.filter(role => evalMap.has(role)).map(role => {
              const ev     = evalMap.get(role)!
              const unconf = notConfiguredReason(ev)
              return (
                <div key={role} className={cn(
                  'rounded-lg border px-4 py-3',
                  unconf ? 'border-bg-border bg-bg-base/40 opacity-80' : 'border-bg-border bg-bg-base',
                )}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-primary">{AGENT_LABELS[role]}</span>
                      {!unconf && <LeanIcon score={ev.score} />}
                    </div>
                    <div className="flex items-center gap-2 text-xs">
                      {unconf ? (
                        <span className="rounded-full border border-bg-border bg-bg-hover px-2 py-0.5 text-[10px] font-medium text-muted">
                          Not configured
                        </span>
                      ) : (
                        <>
                          <span className={cn('font-mono font-bold', bgColorForScore(ev.score).split(' ')[1])}>
                            {ev.score.toFixed(0)}
                          </span>
                          <span className="text-muted">conf {Math.round(ev.confidence * 100)}%</span>
                        </>
                      )}
                    </div>
                  </div>
                  <p className="text-xs text-subtle">
                    {unconf ?? humanizeRationale(role, ev.rationale) ?? AGENT_BLURBS[role]}
                  </p>
                </div>
              )
            })}
            {AGENT_ORDER.filter(role => !evalMap.has(role)).length > 0 && (
              <p className="text-[10px] text-muted pt-1">
                Not evaluated:{' '}
                {AGENT_ORDER.filter(role => !evalMap.has(role)).map(r => AGENT_LABELS[r]).join(', ')}
              </p>
            )}
          </div>
        </div>

        <div className="border-t border-bg-border px-6 py-4">
          <button onClick={onClose} className="btn-ghost w-full">Close</button>
        </div>
      </div>
    </div>
  )
}
