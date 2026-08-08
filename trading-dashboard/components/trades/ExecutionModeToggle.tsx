'use client'
import { useState, useEffect } from 'react'
import { Bot, Hand, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'

/**
 * Toggle between MANUAL approval (bot only suggests; you click Execute on each
 * trade) and AUTO-EXECUTE (orders are placed for you).
 *
 * Saves this user's preference and — for owners — forwards the switch to the
 * bot server's /api/trade-mode, which live_runner and the auto-executor read
 * each cycle. The bot reports back whether its executor is actually armed; if
 * it isn't, say so rather than let auto mode sit there doing nothing.
 */
interface Props {
  onToggle?: (auto: boolean) => void
}

export function ExecutionModeToggle({ onToggle }: Props) {
  const [auto,     setAuto]     = useState<boolean | null>(null)
  const [saving,   setSaving]   = useState(false)
  const [disarmed, setDisarmed] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch('/api/trade-mode', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : { auto_execute: false })
      .then(d => {
        if (cancelled) return
        setAuto(!!d.auto_execute)
        setDisarmed(d.bot?.armed === false ? (d.bot.disarmed_reason ?? 'bot executor disarmed') : null)
      })
      .catch(() => { if (!cancelled) setAuto(false) })
    return () => { cancelled = true }
  }, [])

  async function toggle(next: boolean) {
    if (saving || next === auto) return
    setSaving(true)
    const prev = auto
    setAuto(next)
    try {
      const res  = await fetch('/api/trade-mode', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ auto_execute: next }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`)
      const reason = data.bot?.armed === false ? (data.bot.disarmed_reason ?? 'bot executor disarmed') : null
      setDisarmed(reason)
      onToggle?.(next)
      if (next && reason) {
        toast.warning('Auto-execute ON — but the bot is not armed', {
          description: `${reason}. Trades only run while this page is open.`,
        })
      } else {
        toast.success(next ? 'Auto-execute ON' : 'Manual approval ON', {
          description: next
            ? 'New recommendations will be executed automatically.'
            : 'You approve each trade manually.',
        })
      }
    } catch (err: any) {
      setAuto(prev)
      toast.error('Could not change mode', { description: err?.message || 'Server error' })
    } finally {
      setSaving(false)
    }
  }

  if (auto === null) {
    return (
      <div className="flex items-center gap-1.5 rounded-lg border border-bg-border px-3 py-1.5 text-xs text-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Mode…
      </div>
    )
  }

  return (
    <div
      className={cn(
        'flex items-center gap-1 rounded-lg border p-0.5',
        auto && disarmed ? 'border-caution/50' : 'border-bg-border',
      )}
      title={auto && disarmed
        ? `Bot executor disarmed: ${disarmed}. Trades only run while this page is open.`
        : 'Choose whether the bot executes trades itself or waits for your approval'}
    >
      <button
        onClick={() => toggle(false)}
        disabled={saving}
        className={cn(
          'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-all disabled:opacity-50',
          !auto ? 'bg-caution/15 text-caution' : 'text-muted hover:text-subtle',
        )}
      >
        <Hand className="h-3.5 w-3.5" /> Manual
      </button>
      <button
        onClick={() => toggle(true)}
        disabled={saving}
        className={cn(
          'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-all disabled:opacity-50',
          auto ? 'bg-bull/15 text-bull' : 'text-muted hover:text-subtle',
        )}
      >
        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Bot className="h-3.5 w-3.5" />} Auto
      </button>
    </div>
  )
}
