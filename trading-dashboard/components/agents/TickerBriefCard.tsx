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
  risk:        ['Safe',     'Caution',  'Danger'],
  liquid:      ['High',     'Normal',   'Low'],
  insider:     ['Bullish',  'Neutral',  'Bearish'],
  squeeze:     ['Setup',    'Watch',    'No Setup'],
  macro:       ['Positive', 'Mixed',    'Negative'],
}

function getVerdict(role: string, score: number): string {
  const [pos, mid, neg] = AGENT_VERDICTS[role] ?? ['Strong', 'Neutral', 'Weak']
  if (score >= 65) return pos
  if (score <= 35) return neg
  return mid
}

function AgentRow({ ev }: { ev: AgentEvaluation }) {
  const [stage, setStage] = useState<0 | 1 | 2>(0)
  const verdict = getVerdict(ev.role, ev.score)
  const label   = AGENT_LABELS[ev.role] ?? ev.role
  const hasReasoning = ev.reasoning && Object.keys(ev.reasoning).length > 0

  return (
    <div className="border-t border-bg-border first:border-0">
      <button
        onClick={() => setStage(s => s === 0 ? 1 : 0)}
        className="flex w-full items-center gap-3 px-4 py-2.5 hover:bg-bg-base/60 transition-colors text-left"
      >
        <span className={cn(
          'h-2 w-2 rounded-full shrink-0',
          ev.score >= 65 ? 'bg-bull' : ev.score <= 35 ? 'bg-bear' : 'bg-caution'
        )} />
        <span className="text-xs font-medium text-primary flex-1 min-w-0 truncate">{label}</span>
        <span className={cn(
          'text-[10px] font-semibold shrink-0',
          ev.score >= 65 ? 'text-bull' : ev.score <= 35 ? 'text-bear' : 'text-caution'
        )}>
          {verdict}
        </span>
        <span className={cn('rounded-full border px-1.5 py-0.5 font-mono text-[10px] font-bold shrink-0', bgColorForScore(ev.score))}>
          {ev.score.toFixed(0)}
        </span>
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
  rec: TradeRecommendation
}

export function TickerBriefCard({ rec }: Props) {
  const [showAnalysis, setShowAnalysis] = useState(false)

  const score   = rec.composite_score
  const avgConf = rec.evaluations.length > 0
    ? rec.evaluations.reduce((s, e) => s + e.confidence, 0) / rec.evaluations.length
    : 0

  return (
    <div className="rounded-xl border border-bg-border bg-bg-card overflow-hidden">
      {/* Header */}
      <div className="px-5 pt-4 pb-3 space-y-2">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="ticker-mono text-xl font-bold text-primary tracking-tight">{rec.ticker}</span>
          <span className={cn(
            'rounded-md border px-2.5 py-0.5 text-xs font-bold tracking-wide',
            rec.direction === 'LONG'  ? 'bg-bull/15 text-bull border-bull/30' :
            rec.direction === 'SHORT' ? 'bg-bear/15 text-bear border-bear/30' :
                                        'bg-muted/10 text-muted border-muted/20'
          )}>
            {rec.direction}
          </span>
          <span className={cn('rounded-full border px-2.5 py-0.5 font-mono text-sm font-bold', bgColorForScore(score))}>
            {score.toFixed(0)}
          </span>
          {avgConf > 0 && (
            <span className="text-xs text-muted">conf {Math.round(avgConf * 100)}%</span>
          )}
        </div>
        {rec.rationale && (
          <p className="text-sm text-subtle leading-relaxed italic">{rec.rationale}</p>
        )}
      </div>

      {/* Agent score pills */}
      {rec.evaluations.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-5 pb-3">
          {rec.evaluations.map(ev => (
            <span
              key={ev.role}
              title={AGENT_LABELS[ev.role] ?? ev.role}
              className={cn('rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold', bgColorForScore(ev.score))}
            >
              {(AGENT_LABELS[ev.role] ?? ev.role).split(' ')[0]} {ev.score.toFixed(0)}
            </span>
          ))}
        </div>
      )}

      {/* Analysis toggle */}
      <button
        onClick={() => setShowAnalysis(v => !v)}
        className="flex w-full items-center justify-between border-t border-bg-border px-5 py-2.5 text-xs font-medium text-muted hover:text-primary hover:bg-bg-base/50 transition-colors"
      >
        <span>Show agent analysis</span>
        {showAnalysis
          ? <ChevronDown  className="h-3.5 w-3.5" />
          : <ChevronRight className="h-3.5 w-3.5" />
        }
      </button>

      {/* Agent rows */}
      {showAnalysis && (
        <div className="border-t border-bg-border">
          {rec.evaluations.map(ev => (
            <AgentRow key={ev.role} ev={ev} />
          ))}
          {rec.evaluations.length === 0 && (
            <p className="px-4 py-3 text-xs text-muted">No agent evaluations available.</p>
          )}
        </div>
      )}
    </div>
  )
}
