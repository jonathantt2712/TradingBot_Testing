'use client'
import { useState, useEffect, useRef } from 'react'
import { Trophy, Loader2, Play, RefreshCw, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'

interface Standing {
  challenge_key: string
  return_pct?:   number
  max_drawdown?: number
  rank?:         number | string
  field_size?:   number
  trade_count?:  number
}
interface ChallengeResults {
  mode?:        string
  error?:       string
  standings?:   Standing[]
  submissions?: { challenge_key: string; ticker: string; side: string; composite: number }[]
  updated_at?:  string
}
interface ChallengeStatus {
  last_run_at?: string | null
  last_status?: string | null
  running?:     boolean
  last_error?:  string | null
}

export function ChallengePanel() {
  const [results, setResults] = useState<ChallengeResults | null>(null)
  const [status,  setStatus]  = useState<ChallengeStatus | null>(null)
  const [busy,    setBusy]     = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  async function load() {
    try {
      const res = await fetch('/api/bot/challenges', { cache: 'no-store' })
      if (res.ok) {
        const d = await res.json()
        setResults(d.results ?? null)
        setStatus(d.status ?? null)
      }
    } catch { /* bot offline */ }
  }

  async function run(mode: 'run' | 'status' | 'list') {
    if (busy) return
    setBusy(true)
    try { await fetch(`/api/challenge/run?mode=${mode}`, { method: 'POST' }) } catch { /* queued */ }
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch('/api/bot/challenges', { cache: 'no-store' })
        if (res.ok) {
          const d = await res.json()
          setResults(d.results ?? null)
          setStatus(d.status ?? null)
          if (d.status && d.status.running === false) {
            if (pollRef.current) clearInterval(pollRef.current)
            setBusy(false)
          }
        }
      } catch { /* keep polling */ }
    }, 8_000)
  }

  useEffect(() => { load() }, [])
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  const standings = results?.standings ?? []

  return (
    <div className="card p-5 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-bg-hover">
            <Trophy className="h-4 w-4 text-caution" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-primary">AI4Trade Challenges</h2>
            <p className="text-[10px] text-muted">Benchmark the bot against other AI agents · runs the live agent pipeline</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => run('run')}
            disabled={busy}
            className={cn(
              'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all',
              'bg-caution/15 border border-caution/30 text-caution hover:bg-caution/25',
              'disabled:opacity-50 disabled:cursor-not-allowed',
            )}
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            {busy ? 'Running...' : 'Run Challenge'}
          </button>
          <button onClick={() => run('status')} disabled={busy} className="btn-ghost text-xs">
            <RefreshCw className={cn('h-3.5 w-3.5', busy && 'animate-spin')} />
          </button>
        </div>
      </div>

      {results?.error && (
        <div className="flex items-start gap-2 rounded-lg border border-caution/30 bg-caution/5 px-3 py-2 text-xs">
          <AlertTriangle className="h-4 w-4 text-caution shrink-0 mt-0.5" />
          <p className="text-caution">{results.error}</p>
        </div>
      )}

      {standings.length > 0 ? (
        <div className="rounded-lg border border-bg-border overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-bg-border bg-bg-base">
                <th className="px-3 py-2 text-left  text-muted font-medium">Challenge</th>
                <th className="px-3 py-2 text-right text-muted font-medium">Return</th>
                <th className="px-3 py-2 text-right text-muted font-medium">Max DD</th>
                <th className="px-3 py-2 text-right text-muted font-medium">Rank</th>
              </tr>
            </thead>
            <tbody>
              {standings.map((s, i) => {
                const ret = s.return_pct ?? 0
                return (
                  <tr key={s.challenge_key + i} className={cn('border-b border-bg-border last:border-0', i % 2 ? 'bg-bg-base/50' : '')}>
                    <td className="px-3 py-2 font-mono text-primary truncate max-w-[200px]">{s.challenge_key}</td>
                    <td className={cn('px-3 py-2 text-right font-mono font-semibold', ret >= 0 ? 'text-bull' : 'text-bear')}>
                      {ret >= 0 ? '+' : ''}{ret.toFixed(2)}%
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-bear">{(s.max_drawdown ?? 0).toFixed(2)}%</td>
                    <td className="px-3 py-2 text-right font-mono text-subtle">
                      {s.rank ?? '?'}{s.field_size ? ` / ${s.field_size}` : ''}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : !results?.error ? (
        <p className="text-xs text-muted py-1">
          {status?.last_run_at
            ? 'No standings yet — join a challenge by clicking Run Challenge.'
            : 'Not run yet. Requires AI4TRADE_EMAIL / AI4TRADE_PASSWORD set on the bot server.'}
        </p>
      ) : null}

      {results?.updated_at && (
        <p className="text-[10px] text-muted">Updated {new Date(results.updated_at).toLocaleString()}</p>
      )}
    </div>
  )
}
