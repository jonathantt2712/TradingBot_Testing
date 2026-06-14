'use client'
import { useState } from 'react'
import { X, Eye, EyeOff } from 'lucide-react'
import { toast } from 'sonner'

interface Props {
  onClose: () => void
}

export function ChangePasswordModal({ onClose }: Props) {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [saving, setSaving] = useState(false)

  async function save() {
    if (!currentPassword || !newPassword || !confirmPassword) {
      toast.error('All fields are required')
      return
    }
    if (newPassword !== confirmPassword) {
      toast.error('New passwords do not match')
      return
    }
    setSaving(true)
    try {
      const r = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ currentPassword, newPassword }),
      })
      const d = await r.json()
      if (!r.ok) {
        toast.error(d.error ?? 'Could not change password')
        return
      }
      toast.success('Password updated')
      onClose()
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(2,6,23,0.85)', backdropFilter: 'blur(8px)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-bg-border bg-bg-card shadow-2xl animate-slide-up"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-bg-border px-6 py-4">
          <div>
            <h2 className="text-sm font-semibold text-primary">Change Password</h2>
            <p className="text-xs text-muted">Enter your current and new password</p>
          </div>
          <button onClick={onClose} className="text-muted hover:text-primary transition-colors">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-6 py-4 space-y-2">
          <input
            type={showPassword ? 'text' : 'password'} placeholder="Current password" value={currentPassword} onChange={e => setCurrentPassword(e.target.value)}
            className="w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 text-sm text-primary"
          />
          <input
            type={showPassword ? 'text' : 'password'} placeholder="New password" value={newPassword} onChange={e => setNewPassword(e.target.value)}
            className="w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 text-sm text-primary"
          />
          <div className="relative">
            <input
              type={showPassword ? 'text' : 'password'} placeholder="Confirm new password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)}
              className="w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 pr-9 text-sm text-primary"
            />
            <button
              type="button" onClick={() => setShowPassword(s => !s)} tabIndex={-1}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-subtle"
            >
              {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
        </div>

        <div className="flex gap-2 border-t border-bg-border px-6 py-4">
          <button onClick={onClose} className="btn-ghost flex-1" disabled={saving}>Cancel</button>
          <button onClick={save} disabled={saving} className="btn-primary flex-1 disabled:opacity-50">
            {saving ? 'Saving…' : 'Update password'}
          </button>
        </div>
      </div>
    </div>
  )
}
