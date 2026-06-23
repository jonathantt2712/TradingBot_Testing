'use client'
import { useState, useEffect, useCallback } from 'react'
import { Send, CheckCircle2, Link2Off, ExternalLink, Loader2 } from 'lucide-react'
import { ProfileCard } from './ProfileCard'

type Status = { linked: boolean; activated_at?: string }

export function TelegramCard() {
  const [status, setStatus]       = useState<Status | null>(null)
  const [loading, setLoading]     = useState(true)
  const [working, setWorking]     = useState(false)
  const [linkData, setLinkData]   = useState<{ token: string; bot_username: string } | null>(null)
  const [polling, setPolling]     = useState(false)

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch('/api/bot/telegram', { cache: 'no-store' })
      if (r.ok) setStatus(await r.json())
    } catch {}
    setLoading(false)
  }, [])

  useEffect(() => { fetchStatus() }, [fetchStatus])

  // Poll for activation once a link has been opened
  useEffect(() => {
    if (!polling) return
    const id = setInterval(async () => {
      const r = await fetch('/api/bot/telegram', { cache: 'no-store' })
      if (!r.ok) return
      const s: Status = await r.json()
      if (s.linked) {
        setStatus(s)
        setLinkData(null)
        setPolling(false)
      }
    }, 3000)
    return () => clearInterval(id)
  }, [polling])

  async function connect() {
    setWorking(true)
    try {
      const r = await fetch('/api/bot/telegram', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'register' }),
      })
      if (!r.ok) throw new Error()
      const data = await r.json()
      setLinkData(data)
      setPolling(true)
    } catch {
      alert('Could not reach the bot — make sure it is running.')
    } finally {
      setWorking(false)
    }
  }

  async function unlink() {
    setWorking(true)
    try {
      await fetch('/api/bot/telegram', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'unlink' }),
      })
      setStatus({ linked: false })
      setLinkData(null)
    } catch {}
    setWorking(false)
  }

  const telegramUrl = linkData
    ? `https://t.me/${linkData.bot_username}?start=${linkData.token}`
    : null

  return (
    <ProfileCard title="Telegram Alerts" icon={Send} iconColor="text-brand-cyan">
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Checking status…
        </div>
      ) : status?.linked ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm">
            <CheckCircle2 className="h-4 w-4 text-bull shrink-0" />
            <span className="text-primary font-medium">Telegram connected</span>
          </div>
          {status.activated_at && (
            <p className="text-[11px] text-muted">
              Active since {new Date(status.activated_at + 'Z').toLocaleDateString('en-US', {
                month: 'short', day: 'numeric', year: 'numeric',
              })}
            </p>
          )}
          <p className="text-xs text-subtle">
            You'll receive trade entry &amp; exit alerts, notable market updates,
            and a weekly performance summary every Monday.
          </p>
          <button
            onClick={unlink}
            disabled={working}
            className="flex items-center gap-1.5 text-xs text-bear hover:text-bear/80 transition-colors disabled:opacity-50"
          >
            <Link2Off className="h-3.5 w-3.5" />
            Disconnect Telegram
          </button>
        </div>
      ) : linkData ? (
        <div className="space-y-3">
          <p className="text-xs text-subtle">
            Click the button below to open Telegram, then press <b>Start</b>.
            This page will update automatically once you're connected.
          </p>
          <a
            href={telegramUrl!}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => setPolling(true)}
            className="inline-flex items-center gap-2 rounded-lg bg-brand-cyan/10 border border-brand-cyan/30
                       px-4 py-2 text-sm font-semibold text-brand-cyan hover:bg-brand-cyan/20 transition-colors"
          >
            <ExternalLink className="h-4 w-4" />
            Open Telegram &amp; press Start
          </a>
          <div className="flex items-center gap-1.5 text-[11px] text-muted">
            <Loader2 className="h-3 w-3 animate-spin" />
            Waiting for confirmation…
          </div>
          <p className="text-[11px] text-muted">Link expires in 10 minutes.</p>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-subtle">
            Get instant alerts for every trade the bot makes — entry, exit, market
            events, and a weekly performance summary. All delivered straight to Telegram.
          </p>
          <button
            onClick={connect}
            disabled={working}
            className="flex items-center gap-2 rounded-lg bg-brand-cyan/10 border border-brand-cyan/30
                       px-4 py-2 text-sm font-semibold text-brand-cyan hover:bg-brand-cyan/20
                       transition-colors disabled:opacity-50"
          >
            {working
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Send className="h-4 w-4" />
            }
            {working ? 'Connecting…' : 'Connect Telegram'}
          </button>
        </div>
      )}
    </ProfileCard>
  )
}
