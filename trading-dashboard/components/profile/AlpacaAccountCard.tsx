'use client'
import { useState, useEffect } from 'react'
import { useSession } from 'next-auth/react'
import { Key } from 'lucide-react'
import { toast } from 'sonner'
import { ProfileCard } from './ProfileCard'

export function AlpacaAccountCard() {
  const { update } = useSession()
  const [keyId, setKeyId]   = useState('')
  const [secret, setSecret] = useState('')
  const [paper, setPaper]   = useState(true)
  const [current, setCurrent] = useState<{ alpacaKeyId: string; alpacaPaper: boolean } | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    fetch('/api/alpaca/settings', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) { setCurrent(d); setPaper(d.alpacaPaper) } })
      .catch(() => {})
  }, [])

  async function save() {
    if (!keyId || !secret) {
      toast.error('Key ID and secret are required')
      return
    }
    setSaving(true)
    try {
      const r = await fetch('/api/alpaca/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ alpacaKeyId: keyId, alpacaSecret: secret, alpacaPaper: paper }),
      })
      const d = await r.json()
      if (!r.ok) {
        toast.error(d.error ?? 'Could not save')
        return
      }
      await update({ alpaca: { keyId, secret, paper } })
      toast.success('Alpaca credentials updated')
      setKeyId('')
      setSecret('')
      setCurrent({ alpacaKeyId: keyId, alpacaPaper: paper })
    } finally {
      setSaving(false)
    }
  }

  return (
    <ProfileCard title="Alpaca Account" icon={Key} iconColor="text-brand-cyan">
      {current && (
        <div className="rounded-lg bg-bg-base px-3 py-2 text-xs text-subtle">
          Current key: <span className="font-mono">{current.alpacaKeyId}</span> ({current.alpacaPaper ? 'paper' : 'live'})
        </div>
      )}
      <div className="space-y-2">
        <input
          type="text" placeholder="New Alpaca API Key ID" value={keyId} onChange={e => setKeyId(e.target.value)}
          className="w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 text-sm text-primary font-mono"
        />
        <input
          type="password" placeholder="New Alpaca Secret Key" value={secret} onChange={e => setSecret(e.target.value)}
          className="w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 text-sm text-primary font-mono"
        />
        <div className="flex items-center gap-4 text-xs text-subtle">
          <label className="flex items-center gap-1.5">
            <input type="radio" name="profile-alpaca-paper" checked={paper} onChange={() => setPaper(true)} />
            Paper trading
          </label>
          <label className="flex items-center gap-1.5">
            <input type="radio" name="profile-alpaca-paper" checked={!paper} onChange={() => setPaper(false)} />
            Live trading
          </label>
        </div>
        <button onClick={save} disabled={saving} className="btn-primary text-xs disabled:opacity-50">
          {saving ? 'Saving…' : 'Save Alpaca credentials'}
        </button>
      </div>
    </ProfileCard>
  )
}
