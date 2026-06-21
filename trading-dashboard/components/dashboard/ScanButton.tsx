'use client'
import { useRouter } from 'next/navigation'
import { Radar } from 'lucide-react'
import { useState } from 'react'

type State = 'idle' | 'scanning' | 'done' | 'error'

/** Manually trigger a market scan on the bot, then refresh once it completes.
 *  The scan runs in the background on the bot, so we poll scan-stats'
 *  last_scan_at until it advances (up to ~90s) before refreshing the page. */
export function ScanButton() {
  const router = useRouter()
  const [state, setState] = useState<State>('idle')

  async function lastScanAt(): Promise<string | null> {
    try {
      const r = await fetch('/api/bot/scan-stats', { cache: 'no-store' })
      if (!r.ok) return null
      return (await r.json())?.last_scan_at ?? null
    } catch {
      return null
    }
  }

  async function scan() {
    if (state === 'scanning') return
    setState('scanning')
    const before = await lastScanAt()
    try {
      const res = await fetch('/api/bot/scan', { method: 'POST' })
      if (!res.ok) throw new Error('scan request failed')
    } catch {
      setState('error')
      setTimeout(() => setState('idle'), 3000)
      return
    }
    // Poll for completion (last_scan_at advances), up to ~90s.
    for (let i = 0; i < 30; i++) {
      await new Promise((r) => setTimeout(r, 3000))
      const now = await lastScanAt()
      if (now && now !== before) {
        setState('done')
        router.refresh()
        setTimeout(() => setState('idle'), 2500)
        return
      }
    }
    // Timed out waiting — refresh anyway so any partial results show.
    router.refresh()
    setState('idle')
  }

  const label =
    state === 'scanning' ? 'Scanning…' :
    state === 'done'     ? 'Updated ✓' :
    state === 'error'    ? 'Bot offline' : 'Scan now'

  return (
    <button
      onClick={scan}
      disabled={state === 'scanning'}
      title="Run a fresh market scan now"
      className="btn-ghost text-xs gap-1.5 disabled:opacity-60"
    >
      <Radar className={`h-3.5 w-3.5 ${state === 'scanning' ? 'animate-spin' : ''}`} />
      {label}
    </button>
  )
}
