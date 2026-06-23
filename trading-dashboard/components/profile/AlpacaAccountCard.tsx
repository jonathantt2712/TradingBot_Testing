'use client'
import { useState, useEffect } from 'react'
import { useSession } from 'next-auth/react'
import { Key, X } from 'lucide-react'
import { toast } from 'sonner'
import { ProfileCard } from './ProfileCard'

function UpdateModal({ current, onClose, onSaved }: {
  current: { alpacaKeyId: string; alpacaPaper: boolean } | null
  onClose: () => void
  onSaved: (keyId: string, paper: boolean) => void
}) {
  const { update } = useSession()
  const [keyId,  setKeyId]  = useState('')
  const [secret, setSecret] = useState('')
  const [paper,  setPaper]  = useState(current?.alpacaPaper ?? true)
  const [saving, setSaving] = useState(false)

  async function save() {
    if (!keyId || !secret) { toast.error('Key ID and secret are required'); return }
    setSaving(true)
    try {
      const r = await fetch('/api/alpaca/settings', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ alpacaKeyId: keyId, alpacaSecret: secret, alpacaPaper: paper }),
      })
      const d = await r.json()
      if (!r.ok) { toast.error(d.error ?? 'Could not save'); return }
      await update({ alpaca: { keyId, secret, paper } })
      toast.success('Alpaca credentials updated')
      onSaved(keyId, paper)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="relative w-full max-w-md rounded-2xl border border-bg-border bg-bg-elevated p-6 shadow-card space-y-4 mx-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-primary">Update Alpaca Keys</h2>
            <p className="text-xs text-muted mt-0.5">Enter your new API credentials below</p>
          </div>
          <button onClick={onClose} className="text-muted hover:text-primary transition-colors">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Current key hint */}
        {current && (
          <div className="rounded-lg bg-bg-base border border-bg-border px-3 py-2 text-xs text-subtle">
            Current: <span className="font-mono text-primary">{current.alpacaKeyId}</span>{' '}
            <span className="text-muted">({current.alpacaPaper ? 'paper' : 'live'})</span>
          </div>
        )}

        {/* Form */}
        <div className="space-y-2">
          <input
            type="text"
            placeholder="New Alpaca API Key ID"
            value={keyId}
            onChange={e => setKeyId(e.target.value)}
            className="w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 text-sm text-primary font-mono focus:outline-none focus:border-brand-cyan/50"
          />
          <input
            type="password"
            placeholder="New Alpaca Secret Key"
            value={secret}
            onChange={e => setSecret(e.target.value)}
            className="w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 text-sm text-primary font-mono focus:outline-none focus:border-brand-cyan/50"
          />
          <div className="flex items-center gap-4 text-xs text-subtle pt-1">
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input type="radio" name="modal-alpaca-paper" checked={paper} onChange={() => setPaper(true)} />
              Paper trading
            </label>
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input type="radio" name="modal-alpaca-paper" checked={!paper} onChange={() => setPaper(false)} />
              Live trading
            </label>
          </div>
        </div>

        {/* Actions */}
        <div className="flex gap-2 pt-1">
          <button onClick={onClose} className="btn-ghost text-xs flex-1">Cancel</button>
          <button onClick={save} disabled={saving} className="btn-primary text-xs flex-1 disabled:opacity-50">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

export function AlpacaAccountCard() {
  const [current, setCurrent] = useState<{ alpacaKeyId: string; alpacaPaper: boolean } | null>(null)
  const [modalOpen, setModalOpen] = useState(false)

  useEffect(() => {
    fetch('/api/alpaca/settings', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setCurrent(d) })
      .catch(() => {})
  }, [])

  return (
    <>
      <ProfileCard title="Alpaca Account" icon={Key} iconColor="text-brand-cyan">
        {current ? (
          <div className="rounded-lg bg-bg-base border border-bg-border px-3 py-2 text-xs text-subtle">
            Key: <span className="font-mono text-primary">{current.alpacaKeyId}</span>{' '}
            <span className="text-muted">({current.alpacaPaper ? 'paper' : 'live'})</span>
          </div>
        ) : (
          <p className="text-xs text-muted">No Alpaca credentials saved yet.</p>
        )}
        <button
          onClick={() => setModalOpen(true)}
          className="btn-ghost text-xs w-full mt-1"
        >
          Update Alpaca Keys
        </button>
      </ProfileCard>

      {modalOpen && (
        <UpdateModal
          current={current}
          onClose={() => setModalOpen(false)}
          onSaved={(keyId, paper) => {
            setCurrent({ alpacaKeyId: keyId, alpacaPaper: paper })
            setModalOpen(false)
          }}
        />
      )}
    </>
  )
}
