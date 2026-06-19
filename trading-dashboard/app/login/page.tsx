// trading-dashboard/app/login/page.tsx
'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { signIn } from 'next-auth/react'
import { Zap, Eye, EyeOff } from 'lucide-react'

const COUNTRY_CODES = [
  { name: 'Israel',         code: '+972' },
  { name: 'United States',  code: '+1' },
  { name: 'United Kingdom', code: '+44' },
  { name: 'Canada',         code: '+1' },
  { name: 'Australia',      code: '+61' },
  { name: 'Germany',        code: '+49' },
  { name: 'France',         code: '+33' },
  { name: 'India',          code: '+91' },
]

const PHONE_REGEX = /^\d{6,14}$/

export default function LoginPage() {
  const router = useRouter()
  const [mode, setMode]         = useState<'signin' | 'signup'>('signin')
  const [email, setEmail]       = useState('')
  const [countryCode, setCountryCode] = useState(COUNTRY_CODES[0].code)
  const [phone, setPhone]       = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm]   = useState('')
  const [alpacaKeyId, setAlpacaKeyId]   = useState('')
  const [alpacaSecret, setAlpacaSecret] = useState('')
  const [alpacaPaper, setAlpacaPaper]   = useState(true)
  const [error, setError]     = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  async function handleSignIn(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await signIn('credentials', { email, password, redirect: false })
      if (res?.error) {
        setError('Invalid email or password')
        return
      }
      router.push('/')
      router.refresh()
    } catch {
      setError('Something went wrong — please try again')
    } finally {
      setLoading(false)
    }
  }

  async function handleSignUp(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (password !== confirm) {
      setError('Passwords do not match')
      return
    }
    if (!PHONE_REGEX.test(phone)) {
      setError('Enter a valid phone number (digits only, 6-14 digits)')
      return
    }
    setLoading(true)
    try {
      const res = await fetch('/api/auth/signup', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ email, phone: `${countryCode}${phone}`, password, alpacaKeyId, alpacaSecret, alpacaPaper }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.error ?? 'Could not create account')
        return
      }
      const signInRes = await signIn('credentials', { email, password, redirect: false })
      if (signInRes?.error) {
        setError('Account created — please sign in')
        setMode('signin')
        return
      }
      router.push('/')
      router.refresh()
    } catch {
      setError('Something went wrong — please try again')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg-base px-4 py-4">
      <div className="w-full max-w-sm rounded-2xl border border-bg-border bg-bg-card p-5 space-y-3">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-cyan/10 border border-brand-cyan/30">
            <Zap className="h-3.5 w-3.5 text-brand-cyan" />
          </div>
          <div>
            <p className="text-sm font-semibold text-primary leading-tight">TradingBot</p>
            <p className="text-[10px] text-muted leading-tight">AI Intelligence</p>
          </div>
        </div>

        <div className="flex rounded-lg bg-bg-base p-0.5 text-xs font-medium">
          <button
            type="button"
            onClick={() => { setMode('signin'); setError(null) }}
            className={`flex-1 rounded-md py-1.5 transition-colors ${mode === 'signin' ? 'bg-brand-cyan/10 text-brand-cyan' : 'text-muted'}`}
          >
            Sign in
          </button>
          <button
            type="button"
            onClick={() => { setMode('signup'); setError(null) }}
            className={`flex-1 rounded-md py-1.5 transition-colors ${mode === 'signup' ? 'bg-brand-cyan/10 text-brand-cyan' : 'text-muted'}`}
          >
            Create account
          </button>
        </div>

        <form onSubmit={mode === 'signin' ? handleSignIn : handleSignUp} className="space-y-2">
          <input
            type="email" required placeholder="Email" value={email} onChange={e => setEmail(e.target.value)}
            className="w-full rounded-lg border border-bg-border bg-bg-base px-3 py-1.5 text-sm text-primary placeholder:text-muted"
          />
          <div className="relative">
            <input
              type={showPassword ? 'text' : 'password'} required placeholder="Password" value={password} onChange={e => setPassword(e.target.value)}
              className="w-full rounded-lg border border-bg-border bg-bg-base px-3 py-1.5 pr-9 text-sm text-primary placeholder:text-muted"
            />
            <button
              type="button" onClick={() => setShowPassword(s => !s)} tabIndex={-1}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-subtle"
            >
              {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>

          {mode === 'signin' && (
            <Link href="/forgot-password" className="block text-right text-xs text-brand-cyan hover:underline">
              Forgot password?
            </Link>
          )}

          {mode === 'signup' && (
            <>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'} required placeholder="Confirm password" value={confirm} onChange={e => setConfirm(e.target.value)}
                  className="w-full rounded-lg border border-bg-border bg-bg-base px-3 py-1.5 pr-9 text-sm text-primary placeholder:text-muted"
                />
                <button
                  type="button" onClick={() => setShowPassword(s => !s)} tabIndex={-1}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-subtle"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              <div className="flex gap-2">
                <select
                  value={countryCode} onChange={e => setCountryCode(e.target.value)}
                  className="rounded-lg border border-bg-border bg-bg-base px-2 py-1.5 text-sm text-primary"
                >
                  {COUNTRY_CODES.map(({ name, code }) => (
                    <option key={name} value={code}>{name} ({code})</option>
                  ))}
                </select>
                <input
                  type="tel" required placeholder="Phone number" value={phone} onChange={e => setPhone(e.target.value)}
                  pattern="\d{6,14}" title="Digits only, 6-14 digits"
                  className="w-full rounded-lg border border-bg-border bg-bg-base px-3 py-1.5 text-sm text-primary placeholder:text-muted"
                />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <input
                  type="text" required placeholder="Alpaca Key ID" value={alpacaKeyId} onChange={e => setAlpacaKeyId(e.target.value)}
                  className="w-full rounded-lg border border-bg-border bg-bg-base px-2 py-1.5 text-xs text-primary font-mono placeholder:text-muted"
                />
                <input
                  type="password" required placeholder="Alpaca Secret" value={alpacaSecret} onChange={e => setAlpacaSecret(e.target.value)}
                  className="w-full rounded-lg border border-bg-border bg-bg-base px-2 py-1.5 text-xs text-primary font-mono placeholder:text-muted"
                />
              </div>
              <div className="flex items-center gap-4 text-xs text-subtle">
                <label className="flex items-center gap-1.5">
                  <input type="radio" name="paper" checked={alpacaPaper} onChange={() => setAlpacaPaper(true)} />
                  Paper trading
                </label>
                <label className="flex items-center gap-1.5">
                  <input type="radio" name="paper" checked={!alpacaPaper} onChange={() => setAlpacaPaper(false)} />
                  Live trading
                </label>
              </div>
            </>
          )}

          {error && <p className="text-xs text-bear">{error}</p>}

          <button type="submit" disabled={loading} className="btn-primary w-full disabled:opacity-50">
            {loading ? 'Please wait…' : mode === 'signin' ? 'Sign in' : 'Create account'}
          </button>
        </form>
      </div>
    </div>
  )
}
