'use client'
import { useState, useEffect, useCallback } from 'react'
import {
  TrendingUp, TrendingDown, Minus, ChevronDown, ChevronUp,
  RefreshCw, Wifi, WifiOff, Brain,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { humanizeRationale } from '@/lib/explainAgent'
import type { TradeRecommendation, AgentEvaluation } from '@/types/trading'

// ── Agent metadata ────────────────────────────────────────────────────────────

const AGENT_META: Record<string, { label: string; blurb: string }> = {
  technical:   { label: 'Technical',          blurb: 'Price action, VWAP, relative strength & volume'                           },
  fundamental: { label: 'Fundamental',        blurb: 'News & earnings keyword signals'                                           },
  vision:      { label: 'Vision (Chart)',      blurb: 'Chart pattern recognition via LLM'                                         },
  risk:        { label: 'Risk',               blurb: 'Position sizing, stop placement & R/R viability'                          },
  liquid:      { label: 'Liquidity Flow',     blurb: 'Order flow & liquidity dynamics'                                           },
  social:      { label: 'Social Sentiment',   blurb: 'Community / social sentiment from AI4Trade'                                },
  insider:     { label: 'Congressional Intel',blurb: 'Congressional trading disclosures (House Stock Watcher)'                   },
  squeeze:     { label: 'Short Squeeze',      blurb: 'FINRA daily short volume ratio — squeeze setup detection'                  },
  decision:    { label: 'Master Agent',       blurb: 'LLM synthesises all agent signals and delivers the final trade decision'   },
}
const AGENT_ORDER = ['technical','fundamental','vision','risk','liquid','social','insider','squeeze','decision'] as const

// ── Helpers ───────────────────────────────────────────────────────────────────

function lean(score: number): 'bull' | 'bear' | 'neutral' {
  if (score >= 55) return 'bull'
  if (score <= 45) return 'bear'
  return 'neutral'
}

function LeanIcon({ score, size = 'h-3.5 w-3.5' }: { score: number; size?: string }) {
  const l = lean(score)
  if (l === 'bull') return <TrendingUp  className={cn(size, 'text-bull')} />
  if (l === 'bear') return <TrendingDown className={cn(size, 'text-bear')} />
  return <Minus className={cn(size, 'text-muted')} />
}

function ScoreBadge({ score, large }: { score: number; large?: boolean }) {
  const l = lean(score)
  const color = l === 'bull' ? 'bg-bull/15 text-bull border-bull/30'
              : l === 'bear' ? 'bg-bear/15 text-bear border-bear/30'
              : 'bg-caution/15 text-caution border-caution/30'
  return (
    <div className={cn(
      'flex items-center justify-center rounded-full border font-bold font-mono',
      color,
      large ? 'h-12 w-12 text-lg' : 'h-8 w-8 text-sm',
    )}>
      {Math.round(score)}
    </div>
  )
}

/** Parse social rationale into structured fields for the DETAILS box */
function parseSocialRationale(r: string) {
  const parsed: Record<string, string | number> = {
    signals_analyzed: 0, trade_signals: 0, strategy_signals: 0,
    bull_weight: 0, bear_weight: 0, sentiment_ratio: 0.5,
  }
  const noneM = r.match(/no directional signals \((\d+) signals parsed\)/)
  if (noneM) { parsed.signals_analyzed = parseInt(noneM[1]); return parsed }
  const m = r.match(/bull_w=([\d.]+)\s+bear_w=([\d.]+)\s+\((\d+) trades?,\s*(\d+) strateg/)
  if (m) {
    const [, bw, brw, t, s] = m
    parsed.bull_weight      = parseFloat(bw)
    parsed.bear_weight      = parseFloat(brw)
    parsed.trade_signals    = parseInt(t)
    parsed.strategy_signals = parseInt(s)
    parsed.signals_analyzed = parseInt(t) + parseInt(s)
    const total = parseFloat(bw) + parseFloat(brw)
    parsed.sentiment_ratio  = total > 0 ? parseFloat(bw) / total : 0.5
  }
  return parsed
}

// ── Per-ticker row ────────────────────────────────────────────────────────────

function TickerRow({ ticker, ev }: { ticker: string; ev: AgentEvaluation }) {
  const [open, setOpen] = useState(false)
  const isSocial  = ev.role === 'social'
  const isInsider = ev.role === 'insider'
  const isSqueeze = ev.role === 'squeeze'
  const humanized = humanizeRationale(ev.role, ev.rationale) ?? ev.rationale ?? ''
  const socialData = isSocial && ev.rationale ? parseSocialRationale(ev.rationale) : null

  return (
    <div className="rounded-lg border border-bg-border bg-bg-base">
      <button
        className="flex w-full items-center justify-between px-4 py-2.5 text-left"
        onClick={() => setOpen(v => !v)}
      >
        <div className="flex items-center gap-2.5">
          <span className="font-mono text-sm font-bold text-primary">{ticker}</span>
          <LeanIcon score={ev.score} />
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className={cn('font-mono font-bold', lean(ev.score) === 'bull' ? 'text-bull' : lean(ev.score) === 'bear' ? 'text-bear' : 'text-caution')}>
            {Math.round(ev.score)}
          </span>
          <span className="text-muted">conf {Math.round(ev.confidence * 100)}%</span>
          {open ? <ChevronUp className="h-3.5 w-3.5 text-muted" /> : <ChevronDown className="h-3.5 w-3.5 text-muted" />}
        </div>
      </button>

      {open && (
        <div className="border-t border-bg-border px-4 pb-3 pt-2 space-y-2">
          <p className="text-xs text-subtle">{humanized}</p>

          {/* Social detail box */}
          {isSocial && socialData && (
            <div className="mt-2 space-y-2">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-muted">Details</p>
              <div className="grid grid-cols-3 gap-2">
                {Object.entries(socialData).map(([k, v]) => (
                  <div key={k} className="rounded bg-bg-hover px-2 py-1.5">
                    <p className="text-[9px] text-muted">{k}</p>
                    <p className="text-xs font-mono font-semibold text-subtle">
                      {typeof v === 'number' && k === 'sentiment_ratio' ? v.toFixed(1) : v}
                    </p>
                  </div>
                ))}
              </div>
              <p className="text-[10px] text-muted leading-snug">
                <span className="font-semibold uppercase tracking-wide block mb-0.5">Note</span>
                Signals sourced from AI4Trade community feed. Trade signals (position/trade)
                are full-weight; strategy/discussion signals are half-weight. All signals decay
                exponentially with a 24h half-life.
              </p>
            </div>
          )}

          {/* Insider detail box */}
          {isInsider && ev.rationale && !ev.rationale.includes('no congressional') && (
            <p className="text-[10px] text-muted leading-snug">
              Source: House Stock Watcher (30-day lookback on US congressional trading disclosures)
            </p>
          )}

          {/* Squeeze detail box */}
          {isSqueeze && ev.rationale && !ev.rationale.includes('no FINRA') && (
            <p className="text-[10px] text-muted leading-snug">
              Source: FINRA RegSHO daily short volume. Short ratio = ShortVolume / TotalVolume.
              A ratio above 0.50 with price momentum signals a potential short squeeze setup.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// ── Agent card ────────────────────────────────────────────────────────────────

interface AgentGroup {
  role:    string
  evals:   { ticker: string; ev: AgentEvaluation }[]
  avgScore: number
  avgConf:  number
}

function AgentCard({ group }: { group: AgentGroup }) {
  const [open, setOpen] = useState(false)
  const meta = AGENT_META[group.role] ?? { label: group.role, blurb: '' }
  const isDecision = group.role === 'decision'

  return (
    <div className={cn('card p-0 overflow-hidden', isDecision && 'border-brand-cyan/30')}>
      {isDecision && (
        <div className="flex items-center gap-1.5 bg-brand-cyan/10 px-4 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-brand-cyan">
          <Brain className="h-3 w-3" />
          Master Agent — Final Decision
        </div>
      )}
      <div className="px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <p className="text-sm font-semibold text-primary">{meta.label}</p>
              <LeanIcon score={group.avgScore} />
            </div>
            <p className="mt-0.5 text-xs text-muted">{meta.blurb}</p>
          </div>
          <div className="flex flex-col items-end gap-1 shrink-0">
            <ScoreBadge score={group.avgScore} large={isDecision} />
            <span className="text-[10px] text-muted whitespace-nowrap">
              avg conf {Math.round(group.avgConf * 100)}% · {group.evals.length} {group.evals.length === 1 ? 'ticker' : 'tickers'}
            </span>
          </div>
        </div>

        {group.evals.length > 0 && (
          <button
            onClick={() => setOpen(v => !v)}
            className="mt-3 flex w-full items-center justify-between rounded-lg bg-bg-base px-3 py-2 text-xs text-muted hover:text-subtle transition-colors"
          >
            <span className="font-medium">Per-ticker breakdown</span>
            {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </button>
        )}

        {open && (
          <div className="mt-2 space-y-2">
            {group.evals.map(({ ticker, ev }) => (
              <TickerRow key={ticker} ticker={ticker} ev={ev} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function AgentsPage() {
  const [recs,      setRecs]      = useState<TradeRecommendation[]>([])
  const [loading,   setLoading]   = useState(true)
  const [live,      setLive]      = useState(false)
  const [lastFetch, setLastFetch] = useState<Date | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/bot/recommendations', { cache: 'no-store' })
      if (res.ok) {
        const data = await res.json()
        setRecs(Array.isArray(data) ? data : [])
        setLive(true)
      } else {
        setLive(false)
      }
    } catch {
      setLive(false)
    } finally {
      setLoading(false)
      setLastFetch(new Date())
    }
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [load])

  // Group evaluations by agent role across all recs
  const agentGroups: AgentGroup[] = AGENT_ORDER
    .map(role => {
      const evals: { ticker: string; ev: AgentEvaluation }[] = []
      for (const rec of recs) {
        const ev = (rec.evaluations ?? []).find(e => e.role === role)
        if (ev) evals.push({ ticker: rec.ticker, ev })
      }
      const avgScore = evals.length ? evals.reduce((s, { ev }) => s + ev.score, 0) / evals.length : 50
      const avgConf  = evals.length ? evals.reduce((s, { ev }) => s + ev.confidence, 0) / evals.length : 0
      return { role, evals, avgScore, avgConf }
    })
    .filter(g => g.evals.length > 0)

  const tickerCount = new Set(recs.map(r => r.ticker)).size

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 md:space-y-5 max-w-[900px]">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-primary">Agent Breakdown</h1>
          <p className="text-xs text-muted mt-0.5">
            {tickerCount > 0
              ? `${agentGroups.length} active agents · ${tickerCount} ticker${tickerCount !== 1 ? 's' : ''} scanned`
              : 'Per-agent signal analysis across the current scan'}
            {lastFetch && ` · ${lastFetch.toLocaleTimeString()}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {live
            ? <span className="flex items-center gap-1.5 text-xs text-bull"><Wifi className="h-3 w-3" /> Live</span>
            : <span className="flex items-center gap-1.5 text-xs text-caution"><WifiOff className="h-3 w-3" /> Offline</span>
          }
          <button onClick={load} className="btn-ghost text-xs" disabled={loading}>
            <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          </button>
        </div>
      </div>

      {loading && agentGroups.length === 0 ? (
        <div className="card flex items-center justify-center py-16">
          <RefreshCw className="h-6 w-6 animate-spin text-muted" />
        </div>
      ) : agentGroups.length === 0 ? (
        <div className="card flex flex-col items-center justify-center py-16 text-center">
          <Brain className="h-8 w-8 text-muted mb-3" />
          <p className="text-sm text-muted">
            {live ? 'No scan results yet — trigger a scan from Settings' : 'Bot server offline — cannot load agent data'}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {agentGroups.map(g => <AgentCard key={g.role} group={g} />)}
        </div>
      )}

      {/* Why agents return neutral — explanation box */}
      {live && agentGroups.length > 0 && (
        <div className="rounded-lg border border-bg-border bg-bg-base px-4 py-3 text-[11px] text-muted space-y-1">
          <p className="font-semibold text-subtle">Why some agents show 50 (neutral)</p>
          <p><span className="text-caution">Vision "vision error → neutral"</span> — No vision API key set on Railway (GEMINI_API_KEY or ANTHROPIC_API_KEY missing).</p>
          <p><span className="text-caution">Social "no directional signals"</span> — AI4Trade found posts but none contain clear bullish/bearish trade signals.</p>
          <p><span className="text-caution">Technical "stale data"</span> — Market is closed; bars are from the previous session. Scores update when the market opens.</p>
          <p><span className="text-caution">Insider / Squeeze absent</span> — No data found for the scanned tickers (normal for most stocks; activates on high-short or congress-traded names).</p>
        </div>
      )}
    </div>
  )
}
