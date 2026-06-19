'use client'
import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { cn, bgColorForScore } from '@/lib/utils'
import { AGENT_LABELS } from '@/lib/agents'
import { ReasoningDetail } from './ReasoningDetail'
import type { TradeRecommendation, AgentEvaluation } from '@/types/trading'

const AGENT_VERDICTS: Record<string, [string, string, string]> = {
  technical:   ['Bullish',  'Neutral',  'Bearish'],
  fundamental: ['Positive', 'Mixed',    'Negative'],
  vision:      ['Bullish',  'Unclear',  'Bearish'],
  risk:        ['Safe',     'Caution',  'Risky'],
  liquid:      ['High',     'Normal',   'Low'],
  insider:     ['Bullish',  'Neutral',  'Bearish'],
  squeeze:     ['Setup',    'Watch',    'No Setup'],
  macro:       ['Positive', 'Mixed',    'Negative'],
  social:      ['Positive', 'Quiet',    'Negative'],
}

function getVerdict(role: string, score: number): string {
  const [pos, mid, neg] = AGENT_VERDICTS[role] ?? ['Strong', 'Neutral', 'Weak']
  if (score >= 65) return pos
  if (score <= 35) return neg
  return mid
}

function makeBrief(role: string, entries: { ticker: string; ev: AgentEvaluation }[]): string {
  if (entries.length === 0) return 'No tickers analyzed this period.'
  const avg    = entries.reduce((s, e) => s + e.ev.score, 0) / entries.length
  const sorted = [...entries].sort((a, b) => b.ev.score - a.ev.score)
  const top    = sorted[0]
  const bottom = sorted[sorted.length - 1]
  const n      = entries.length
  const verdict = getVerdict(role, avg)

  // Prefer the top ticker's own rationale if it reads like a real sentence
  const rat = top.ev.rationale ?? ''
  const isUsable = rat.length > 15 && !rat.startsWith('fallback') && !rat.startsWith('[keyword]') && !rat.startsWith('Price >')

  if (avg >= 65) {
    if (isUsable) return `${top.ticker}: ${rat}.`
    return `${verdict} signals across ${n} ticker${n > 1 ? 's' : ''}. ${top.ticker} leads at ${top.ev.score.toFixed(0)}.`
  }
  if (avg <= 35) {
    return `${verdict} conditions across ${n} ticker${n > 1 ? 's' : ''}. ${bottom.ticker} shows the weakest reading at ${bottom.ev.score.toFixed(0)}.`
  }
  return `Mixed signals across ${n} ticker${n > 1 ? 's' : ''}. ${top.ticker} is the strongest at ${top.ev.score.toFixed(0)}, but no clear directional bias yet.`
}

function TickerRow({ ticker, ev }: { ticker: string; ev: AgentEvaluation }) {
  const [stage, setStage] = useState<0 | 1 | 2>(0)
  const hasReasoning = ev.reasoning && Object.keys(ev.reasoning).length > 0

  return (
    <div className="border-t border-bg-border first:border-0">
      <button
        onClick={() => setStage(s => s === 0 ? 1 : 0)}
        className="flex w-full items-center gap-3 px-4 py-2.5 hover:bg-bg-base/60 transition-colors text-left"
      >
        <span className="ticker-mono text-sm font-bold text-primary w-16 shrink-0">{ticker}</span>
        <span className={cn('rounded-full border px-2 py-0.5 font-mono text-[10px] font-bold shrink-0', bgColorForScore(ev.score))}>
          {ev.score.toFixed(0)}
        </span>
        <span className={cn(
          'text-[10px] font-semibold shrink-0',
          ev.score >= 65 ? 'text-bull' : ev.score <= 35 ? 'text-bear' : 'text-caution'
        )}>
          {getVerdict('', ev.score)}
        </span>
        <span className="text-xs text-muted flex-1 min-w-0 truncate italic">{ev.rationale ?? ''}</span>
        {stage === 0
          ? <ChevronRight className="h-3.5 w-3.5 text-muted shrink-0" />
          : <ChevronDown  className="h-3.5 w-3.5 text-subtle shrink-0" />
        }
      </button>

      {stage >= 1 && (
        <div className="px-4 pb-3 space-y-2">
          {ev.rationale
            ? <p className="text-xs text-subtle leading-relaxed italic">{ev.rationale}</p>
            : <p className="text-xs text-muted italic">No brief available.</p>
          }
          {stage === 1 && hasReasoning && (
            <button
              onClick={e => { e.stopPropagation(); setStage(2) }}
              className="text-[10px] text-brand-cyan hover:underline"
            >
              Full analysis →
            </button>
          )}
          {stage === 2 && <ReasoningDetail reasoning={ev.reasoning} />}
        </div>
      )}
    </div>
  )
}

interface Props {
  role:            string
  recommendations: TradeRecommendation[]
  loading?:        boolean
}

export function AgentOverviewCard({ role, recommendations, loading }: Props) {
  const [expanded, setExpanded] = useState(false)

  const entries: { ticker: string; ev: AgentEvaluation }[] = []
  for (const rec of recommendations) {
    const ev = rec.evaluations.find(e => e.role === role)
    if (ev) entries.push({ ticker: rec.ticker, ev })
  }

  const label = AGENT_LABELS[role] ?? role

  if (entries.length === 0) {
    return (
      <div className="rounded-xl border border-bg-border bg-bg-card px-5 py-4 flex items-center justify-between gap-4">
        <h3 className="text-sm font-semibold text-primary">{label}</h3>
        <p className="text-xs text-muted">
          {loading ? 'Loading…' : 'No analysis from last scan'}
        </p>
      </div>
    )
  }

  const avgScore = entries.reduce((s, e) => s + e.ev.score, 0) / entries.length
  const verdict  = getVerdict(role, avgScore)
  const brief    = makeBrief(role, entries)

  return (
    <div className="rounded-xl border border-bg-border bg-bg-card overflow-hidden">
      {/* Header */}
      <div className="px-5 pt-4 pb-3 space-y-2">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold text-primary flex-1 min-w-0">{label}</h3>
          <span className={cn(
            'text-xs font-semibold shrink-0',
            avgScore >= 65 ? 'text-bull' : avgScore <= 35 ? 'text-bear' : 'text-caution'
          )}>
            {verdict}
          </span>
          <span className={cn('rounded-full border px-2.5 py-0.5 font-mono text-sm font-bold shrink-0', bgColorForScore(avgScore))}>
            {avgScore.toFixed(0)}
          </span>
        </div>

        {/* Brief */}
        <p className="text-sm text-subtle leading-relaxed italic">{brief}</p>

        {/* Ticker score pills */}
        <div className="flex flex-wrap gap-1.5 pt-0.5">
          {entries.map(({ ticker, ev }) => (
            <span
              key={ticker}
              title={`${ticker}: ${ev.score.toFixed(0)}`}
              className={cn('rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold', bgColorForScore(ev.score))}
            >
              {ticker} {ev.score.toFixed(0)}
            </span>
          ))}
        </div>
      </div>

      {/* Expand toggle */}
      <button
        onClick={() => setExpanded(v => !v)}
        className="flex w-full items-center justify-between border-t border-bg-border px-5 py-2.5 text-xs font-medium text-muted hover:text-primary hover:bg-bg-base/50 transition-colors"
      >
        <span>What I saw per ticker</span>
        {expanded
          ? <ChevronDown  className="h-3.5 w-3.5" />
          : <ChevronRight className="h-3.5 w-3.5" />
        }
      </button>

      {/* Per-ticker rows */}
      {expanded && (
        <div className="border-t border-bg-border">
          {entries.map(({ ticker, ev }) => (
            <TickerRow key={ticker} ticker={ticker} ev={ev} />
          ))}
        </div>
      )}
    </div>
  )
}
