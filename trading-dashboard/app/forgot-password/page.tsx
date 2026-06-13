// trading-dashboard/app/forgot-password/page.tsx
'use client'
import { useState } from 'react'
import Link from 'next/link'
import { Zap } from 'lucide-react'

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [loading, setLoading] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await fetch('/api/auth/forgot-password', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ email }),
      })
      setSubmitted(true)
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

        {submitted ? (
          <p className="text-sm text-subtle">
            If an account exists for that email, we&apos;ve sent a link to reset your password.
          </p>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label className="text-xs text-muted">Email</label>
              <input
                type="email" required value={email} onChange={e => setEmail(e.target.value)}
                className="mt-1 w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 text-sm text-primary"
              />
            </div>
            {error && <p className="text-xs text-bear">{error}</p>}
            <button type="submit" disabled={loading} className="btn-primary w-full disabled:opacity-50">
              {loading ? 'Please wait…' : 'Send reset link'}
            </button>
          </form>
        )}

        <Link href="/login" className="block text-center text-xs text-brand-cyan hover:underline">
          Back to sign in
        </Link>
      </div>
    </div>
  )
}
