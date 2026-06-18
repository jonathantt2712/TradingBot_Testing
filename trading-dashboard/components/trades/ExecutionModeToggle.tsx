'use client'
import { useState, useEffect } from 'react'
import { Bot, Hand, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'

/**
 * Toggle between MANUAL approval (bot only suggests; you click Execute on each
 * trade) and AUTO-EXECUTE (the bot places buy/sell orders itself).
 *
 * Writes to the bot server's /api/trade-mode, which live_runner reads each
 * scan — the switch takes effect within one cycle, no redeploy.
 */
export function ExecutionModeToggle() {
  const [auto,    setAuto]    = useState<boolean | null>(null)
  const [saving,  setSaving]  = useState(false)

  useEffect(() => {
    let cancelled = false
    fetch('/api/trade-mode', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : { auto_execute: false })
      .then(d => { if (!cancelled) setAuto(!!d.auto_execute) })
      .catch(() => { if (!cancelled) setAuto(false) })
    return () => { cancelled = true }
  }, [])

  async function toggle(next: boolean) {
    if (saving || next === auto) return
    setSaving(true)
    const prev = auto
    setAuto(next)  // optimistic
    try {
      const res = await fetch('/api/trade-mode', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ auto_execute: next }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      toast.success(next ? 'Auto-execute ON' : 'Manual approval ON', {
        description: next
          ? 'The bot will place buy/sell orders by itself.'
          : 'The bot will only suggest trades — you approve each one.',
      })
    } catch (err: any) {
      setAuto(prev)  // rollback
      toast.error('Could not change mode', { description: err?.message || 'Bot offline' })
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
    <div className="flex items-center gap-1 rounded-lg border border-bg-border p-0.5" title="Choose whether the bot executes trades itself or waits for your approval">
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
