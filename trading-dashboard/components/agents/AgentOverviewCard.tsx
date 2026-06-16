'use client'
import { useState } from 'react'
import { ChevronDown, ChevronUp, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { cn, bgColorForScore } from '@/lib/utils'
import { AGENT_LABELS, AGENT_BLURBS } from '@/lib/agents'
import { ReasoningDetail } from './ReasoningDetail'
import type { TradeRecommendation, AgentEvaluation } from '@/types/trading'

interface Props {
  role:            string
  recommendations: TradeRecommendation[]
}

function lean(score: number): 'bull' | 'bear' | 'neutral' {
  if (score >= 55) return 'bull'
  if (score <= 45) return 'bear'
  return 'neutral'
}

function LeanIcon({ score }: { score: number }) {
  const l = lean(score)
  if (l === 'bull') return <TrendingUp className="h-3.5 w-3.5 text-bull" />
  if (l === 'bear') return <TrendingDown className="h-3.5 w-3.5 text-bear" />
  return <Minus className="h-3.5 w-3.5 text-muted" />
}

export function AgentOverviewCard({ role, recommendations }: Props) {
  const [expanded, setExpanded] = useState(false)

  const entries: { ticker: string; ev: AgentEvaluation }[] = []
  for (const rec of recommendations) {
    const ev = rec.evaluations.find(e => e.role === role)
    if (ev) entries.push({ ticker: rec.ticker, ev })
  }

  if (entries.length === 0) {
    return (
      <div className="card space-y-1">
        <h3 className="text-sm font-semibold text-primary">{AGENT_LABELS[role] ?? role}</h3>
        <p className="text-xs text-muted">{AGENT_BLURBS[role]}</p>
        <p className="text-xs text-muted pt-2">No data for this agent right now.</p>
      </div>
    )
  }

  const avgScore = entries.reduce((s, { ev }) => s + ev.score, 0) / entries.length
  const avgConf  = entries.reduce((s, { ev }) => s + ev.confidence, 0) / entries.length

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-primary">{AGENT_LABELS[role] ?? role}</h3>
            <LeanIcon score={avgScore} />
          </div>
          <p className="text-xs text-muted mt-0.5">{AGENT_BLURBS[role]}</p>
        </div>
        <div className="text-right">
          <span className={cn('inline-block rounded-full border px-2 py-1 font-mono text-sm font-bold', bgColorForScore(avgScore))}>
            {avgScore.toFixed(0)}
          </span>
          <p className="text-[10px] text-muted mt-1">avg conf {Math.round(avgConf * 100)}% · {entries.length} ticker{entries.length === 1 ? '' : 's'}</p>
        </div>
      </div>

      <button
        onClick={() => setExpanded(v => !v)}
        className="flex w-full items-center justify-between text-xs font-semibold text-subtle hover:text-primary transition-colors"
      >
        <span>Per-ticker breakdown</span>
        {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
      </button>

      {expanded && (
        <div className="space-y-3">
          {entries.map(({ ticker, ev }) => (
            <div key={ticker} className="rounded-lg border border-bg-border bg-bg-base px-3 py-2 space-y-2">
              <div className="flex items-center justify-between">
                <span className="ticker-mono text-sm text-primary">{ticker}</span>
                <div className="flex items-center gap-2 text-xs">
                  <span className={cn('font-mono font-bold', bgColorForScore(ev.score).split(' ')[1])}>{ev.score.toFixed(0)}</span>
                  <span className="text-muted">conf {Math.round(ev.confidence * 100)}%</span>
                </div>
              </div>
              {ev.rationale && <p className="text-xs text-subtle">{ev.rationale}</p>}
              <ReasoningDetail reasoning={ev.reasoning} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
