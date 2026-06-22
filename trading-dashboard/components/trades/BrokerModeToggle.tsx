'use client'
import { useState, useEffect } from 'react'
import { Landmark, Server, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'

type Broker = 'alpaca' | 'ibkr'

/**
 * Switch the execution venue between Alpaca and IBKR (TWS/IB Gateway).
 *
 * Writes to the bot server's /api/broker-mode. live_runner polls it and
 * restarts its trading session on the new broker — flattening open positions
 * first when auto-execute is live, so nothing is orphaned across venues. That
 * makes this a more consequential switch than the manual/auto toggle, so it
 * confirms before changing.
 */
export function BrokerModeToggle() {
  const [broker, setBroker] = useState<Broker | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetch('/api/broker-mode', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : { broker: 'alpaca' })
      .then(d => { if (!cancelled) setBroker(d.broker === 'ibkr' ? 'ibkr' : 'alpaca') })
      .catch(() => { if (!cancelled) setBroker('alpaca') })
    return () => { cancelled = true }
  }, [])

  async function choose(next: Broker) {
    if (saving || next === broker) return
    const label = next === 'ibkr' ? 'IBKR (TWS)' : 'Alpaca'
    if (!window.confirm(
      `Switch execution to ${label}?\n\n` +
      'The bot will restart its trading session on the new broker. If ' +
      'auto-execute is on, any open positions are flattened first so they are ' +
      'not orphaned on the old venue.'
    )) return

    setSaving(true)
    const prev = broker
    setBroker(next)  // optimistic
    try {
      const res = await fetch('/api/broker-mode', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ broker: next }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      toast.success(`Broker → ${label}`, {
        description: next === 'ibkr'
          ? 'Requires TWS / IB Gateway running on the bot PC with the API enabled.'
          : 'Routing orders through Alpaca.',
      })
    } catch (err: any) {
      setBroker(prev)  // rollback
      toast.error('Could not switch broker', { description: err?.message || 'Bot offline' })
    } finally {
      setSaving(false)
    }
  }

  if (broker === null) {
    return (
      <div className="flex items-center gap-1.5 rounded-lg border border-bg-border px-3 py-1.5 text-xs text-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Broker…
      </div>
    )
  }

  return (
    <div className="flex items-center gap-1 rounded-lg border border-bg-border p-0.5" title="Choose which brokerage executes orders">
      <button
        onClick={() => choose('alpaca')}
        disabled={saving}
        className={cn(
          'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-all disabled:opacity-50',
          broker === 'alpaca' ? 'bg-brand-cyan/15 text-brand-cyan' : 'text-muted hover:text-subtle',
        )}
      >
        <Landmark className="h-3.5 w-3.5" /> Alpaca
      </button>
      <button
        onClick={() => choose('ibkr')}
        disabled={saving}
        className={cn(
          'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-all disabled:opacity-50',
          broker === 'ibkr' ? 'bg-bull/15 text-bull' : 'text-muted hover:text-subtle',
        )}
      >
        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Server className="h-3.5 w-3.5" />} IBKR
      </button>
    </div>
  )
}
