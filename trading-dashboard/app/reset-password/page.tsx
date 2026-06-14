// trading-dashboard/app/reset-password/page.tsx
'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { useSession } from 'next-auth/react'
import { Zap, Eye, EyeOff } from 'lucide-react'

export default function ResetPasswordPage() {
  const router = useRouter()
  const { update } = useSession()

  const [password, setPassword] = useState('')
  const [confirm, setConfirm]   = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError]     = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (password !== confirm) {
      setError('Passwords do not match')
      return
    }
    setLoading(true)
    try {
      const res = await fetch('/api/auth/reset-password', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ password }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.error ?? 'Could not update password')
        return
      }
      await update({ mustChangePassword: false })
      router.push('/')
    } catch {
      setError('Something went wrong — please try again')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg-base px-4">
      <div className="w-full max-w-sm rounded-2xl border border-bg-border bg-bg-card p-6 space-y-5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-cyan/10 border border-brand-cyan/30">
            <Zap className="h-4 w-4 text-brand-cyan" />
          </div>
          <div>
            <p className="text-sm font-semibold text-primary leading-tight">TradingBot</p>
            <p className="text-[10px] text-muted leading-tight">AI Intelligence</p>
          </div>
        </div>

        <div>
          <h1 className="text-sm font-semibold text-primary">Choose a new password</h1>
          <p className="text-xs text-muted mt-0.5">
            You&apos;re signed in with a temporary password. Set a new password to continue.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="text-xs text-muted">New password</label>
            <div className="relative mt-1">
              <input
                type={showPassword ? 'text' : 'password'} required value={password} onChange={e => setPassword(e.target.value)}
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
          <div>
            <label className="text-xs text-muted">Confirm new password</label>
            <div className="relative mt-1">
              <input
                type={showPassword ? 'text' : 'password'} required value={confirm} onChange={e => setConfirm(e.target.value)}
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
          {error && <p className="text-xs text-bear">{error}</p>}
          <button type="submit" disabled={loading} className="btn-primary w-full disabled:opacity-50">
            {loading ? 'Please wait…' : 'Set new password'}
          </button>
        </form>
      </div>
    </div>
  )
}
