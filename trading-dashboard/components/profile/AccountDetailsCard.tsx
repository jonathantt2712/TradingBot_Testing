'use client'
import { useState } from 'react'
import { Smartphone } from 'lucide-react'
import { toast } from 'sonner'
import { ProfileCard } from './ProfileCard'

interface Props {
  initialPhone: string
}

export function AccountDetailsCard({ initialPhone }: Props) {
  const [phone, setPhone] = useState(initialPhone)
  const [saving, setSaving] = useState(false)

  async function save() {
    setSaving(true)
    try {
      const r = await fetch('/api/auth/profile', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone }),
      })
      const d = await r.json()
      if (!r.ok) {
        toast.error(d.error ?? 'Could not update phone number')
        return
      }
      toast.success('Phone number updated')
      setPhone(d.phone ?? '')
    } finally {
      setSaving(false)
    }
  }

  return (
    <ProfileCard title="Account Details" icon={Smartphone} iconColor="text-brand-cyan">
      <div className="space-y-2">
        <label className="text-xs text-subtle">Phone number</label>
        <input
          type="text" placeholder="+972501234567" value={phone} onChange={e => setPhone(e.target.value)}
          className="w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 text-sm text-primary font-mono"
        />
        <button onClick={save} disabled={saving} className="btn-ghost text-xs w-full disabled:opacity-50">
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </ProfileCard>
  )
}
