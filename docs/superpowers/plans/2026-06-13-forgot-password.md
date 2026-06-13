# Forgot Password Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user reset a forgotten password via an emailed link, then sign in with the new password.

**Architecture:** Add a hashed reset token + expiry to `User`. `/forgot-password` requests a token and emails a link via Brevo; `/reset-password?token=...` validates the token and updates the password. Both new pages are public (no session required).

**Tech Stack:** Next.js 14 App Router, Prisma/Postgres, bcryptjs, Vitest, Brevo transactional email REST API (via `fetch`, no new dependency).

---

## Spec Reference

Full design: `docs/superpowers/specs/2026-06-13-forgot-password-design.md`

## File Structure

- Modify: `trading-dashboard/prisma/schema.prisma` — add `resetTokenHash`, `resetTokenExpiry` to `User`
- Create: `trading-dashboard/lib/resetToken.ts` — token generation/hashing helpers
- Create: `trading-dashboard/tests/lib/resetToken.test.ts`
- Create: `trading-dashboard/lib/brevo.ts` — `sendPasswordResetEmail`
- Create: `trading-dashboard/app/api/auth/forgot-password/route.ts`
- Create: `trading-dashboard/tests/api/forgot-password.test.ts`
- Create: `trading-dashboard/app/api/auth/reset-password/route.ts`
- Create: `trading-dashboard/tests/api/reset-password.test.ts`
- Create: `trading-dashboard/app/forgot-password/page.tsx`
- Create: `trading-dashboard/app/reset-password/page.tsx`
- Modify: `trading-dashboard/middleware.ts` — allow new public paths
- Modify: `trading-dashboard/app/login/page.tsx` — add "Forgot password?" link + reset-success message

---

### Task 1: Add reset token fields to the User model

**Files:**
- Modify: `trading-dashboard/prisma/schema.prisma`

- [ ] **Step 1: Add the fields**

In `trading-dashboard/prisma/schema.prisma`, update the `User` model from:

```prisma
model User {
  id           String   @id @default(uuid())
  email        String   @unique
  phone        String?
  passwordHash String
  alpacaKeyId  String
  alpacaSecret String
  alpacaPaper  Boolean  @default(true)
  createdAt    DateTime @default(now())
}
```

to:

```prisma
model User {
  id               String    @id @default(uuid())
  email            String    @unique
  phone            String?
  passwordHash     String
  alpacaKeyId      String
  alpacaSecret     String
  alpacaPaper      Boolean   @default(true)
  resetTokenHash   String?
  resetTokenExpiry DateTime?
  createdAt        DateTime  @default(now())
}
```

- [ ] **Step 2: Run the migration against the live Neon DB**

Run from `trading-dashboard/`:

```bash
export $(grep -v '^#' .env.local | grep DATABASE_URL | xargs -d '\n')
npx prisma migrate dev --name add_password_reset_token
```

Expected: a new migration directory appears under `prisma/migrations/`, and the command prints "Your database is now in sync with your schema."

- [ ] **Step 3: Regenerate the Prisma client**

```bash
npx prisma generate
```

Expected: "Generated Prisma Client" with no errors. (If it errors with `EPERM` on `query_engine-windows.dll.node` because a dev server is running, stop the dev server first, then re-run.)

- [ ] **Step 4: Verify the client picked up the new fields**

```bash
npx tsc --noEmit
```

Expected: no errors (this just confirms the schema/client are in sync; the new fields aren't referenced anywhere yet).

- [ ] **Step 5: Commit**

```bash
git add prisma/schema.prisma prisma/migrations
git commit -m "feat: add password reset token fields to User model"
```

---

### Task 2: Reset token helpers

**Files:**
- Create: `trading-dashboard/lib/resetToken.ts`
- Test: `trading-dashboard/tests/lib/resetToken.test.ts`

- [ ] **Step 1: Write the failing test**

Create `trading-dashboard/tests/lib/resetToken.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { generateResetToken, hashResetToken, RESET_TOKEN_TTL_MS } from '@/lib/resetToken'

describe('lib/resetToken', () => {
  it('generates a token whose hash matches hashResetToken', () => {
    const { token, hash } = generateResetToken()
    expect(hashResetToken(token)).toBe(hash)
  })

  it('generates different tokens each time', () => {
    const a = generateResetToken()
    const b = generateResetToken()
    expect(a.token).not.toBe(b.token)
    expect(a.hash).not.toBe(b.hash)
  })

  it('sets an expiry roughly 1 hour in the future', () => {
    const { expiresAt } = generateResetToken()
    const diff = expiresAt.getTime() - Date.now()
    expect(diff).toBeGreaterThan(RESET_TOKEN_TTL_MS - 5000)
    expect(diff).toBeLessThanOrEqual(RESET_TOKEN_TTL_MS)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run tests/lib/resetToken.test.ts
```

Expected: FAIL — `Cannot find module '@/lib/resetToken'`

- [ ] **Step 3: Write the implementation**

Create `trading-dashboard/lib/resetToken.ts`:

```ts
import crypto from 'crypto'

export const RESET_TOKEN_TTL_MS = 60 * 60 * 1000 // 1 hour

export function hashResetToken(token: string): string {
  return crypto.createHash('sha256').update(token).digest('hex')
}

export function generateResetToken(): { token: string; hash: string; expiresAt: Date } {
  const token = crypto.randomBytes(32).toString('hex')
  return {
    token,
    hash: hashResetToken(token),
    expiresAt: new Date(Date.now() + RESET_TOKEN_TTL_MS),
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run tests/lib/resetToken.test.ts
```

Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add lib/resetToken.ts tests/lib/resetToken.test.ts
git commit -m "feat: add password reset token generation helpers"
```

---

### Task 3: Brevo email sender

**Files:**
- Create: `trading-dashboard/lib/brevo.ts`

- [ ] **Step 1: Write the implementation**

Create `trading-dashboard/lib/brevo.ts`:

```ts
const BREVO_API_URL = 'https://api.brevo.com/v3/smtp/email'

export async function sendPasswordResetEmail(to: string, resetUrl: string): Promise<void> {
  const apiKey = process.env.BREVO_API_KEY
  const senderEmail = process.env.BREVO_SENDER_EMAIL
  if (!apiKey || !senderEmail) {
    throw new Error('BREVO_API_KEY and BREVO_SENDER_EMAIL must be set')
  }

  const res = await fetch(BREVO_API_URL, {
    method: 'POST',
    headers: {
      'api-key': apiKey,
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({
      sender: { email: senderEmail, name: 'TradingBot' },
      to: [{ email: to }],
      subject: 'Reset your TradingBot password',
      htmlContent: `
        <p>We received a request to reset your TradingBot password.</p>
        <p><a href="${resetUrl}">Click here to choose a new password</a></p>
        <p>This link expires in 1 hour. If you didn't request this, you can ignore this email.</p>
      `,
    }),
  })

  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`Brevo API error ${res.status}: ${body}`)
  }
}
```

- [ ] **Step 2: Type-check**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add lib/brevo.ts
git commit -m "feat: add Brevo password reset email sender"
```

---

### Task 4: `POST /api/auth/forgot-password`

**Files:**
- Create: `trading-dashboard/app/api/auth/forgot-password/route.ts`
- Test: `trading-dashboard/tests/api/forgot-password.test.ts`

- [ ] **Step 1: Write the failing test**

Create `trading-dashboard/tests/api/forgot-password.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/lib/prisma', () => ({
  prisma: {
    user: {
      findUnique: vi.fn(),
      update: vi.fn(),
    },
  },
}))
vi.mock('@/lib/brevo', () => ({
  sendPasswordResetEmail: vi.fn(),
}))

import { prisma } from '@/lib/prisma'
import { sendPasswordResetEmail } from '@/lib/brevo'
import { POST } from '@/app/api/auth/forgot-password/route'

function makeRequest(body: unknown) {
  return new Request('http://localhost/api/auth/forgot-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', origin: 'http://localhost:3000' },
    body: JSON.stringify(body),
  })
}

describe('POST /api/auth/forgot-password', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns success without sending an email when the user does not exist', async () => {
    vi.mocked(prisma.user.findUnique).mockResolvedValue(null)

    const res = await POST(makeRequest({ email: 'nobody@example.com' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true })
    expect(prisma.user.update).not.toHaveBeenCalled()
    expect(sendPasswordResetEmail).not.toHaveBeenCalled()
  })

  it('stores a reset token and sends an email when the user exists', async () => {
    vi.mocked(prisma.user.findUnique).mockResolvedValue({ id: 'user-1', email: 'real@example.com' } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({} as any)
    vi.mocked(sendPasswordResetEmail).mockResolvedValue(undefined)

    const res = await POST(makeRequest({ email: 'real@example.com' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true })
    expect(prisma.user.update).toHaveBeenCalledWith({
      where: { id: 'user-1' },
      data: expect.objectContaining({
        resetTokenHash: expect.any(String),
        resetTokenExpiry: expect.any(Date),
      }),
    })
    expect(sendPasswordResetEmail).toHaveBeenCalledWith(
      'real@example.com',
      expect.stringContaining('http://localhost:3000/reset-password?token=')
    )
  })

  it('returns success even if sending the email fails', async () => {
    vi.mocked(prisma.user.findUnique).mockResolvedValue({ id: 'user-1', email: 'real@example.com' } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({} as any)
    vi.mocked(sendPasswordResetEmail).mockRejectedValue(new Error('Brevo down'))

    const res = await POST(makeRequest({ email: 'real@example.com' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run tests/api/forgot-password.test.ts
```

Expected: FAIL — `Cannot find module '@/app/api/auth/forgot-password/route'`

- [ ] **Step 3: Write the implementation**

Create `trading-dashboard/app/api/auth/forgot-password/route.ts`:

```ts
// trading-dashboard/app/api/auth/forgot-password/route.ts
import { NextResponse } from 'next/server'
import { prisma } from '@/lib/prisma'
import { generateResetToken } from '@/lib/resetToken'
import { sendPasswordResetEmail } from '@/lib/brevo'

export async function POST(req: Request) {
  const body = await req.json().catch(() => null) as { email?: string } | null
  const email = body?.email

  if (email) {
    const user = await prisma.user.findUnique({ where: { email } })
    if (user) {
      const { token, hash, expiresAt } = generateResetToken()
      await prisma.user.update({
        where: { id: user.id },
        data: { resetTokenHash: hash, resetTokenExpiry: expiresAt },
      })

      const origin = req.headers.get('origin') ?? new URL(req.url).origin
      const resetUrl = `${origin}/reset-password?token=${token}`

      try {
        await sendPasswordResetEmail(email, resetUrl)
      } catch (err) {
        console.error('Failed to send password reset email', err)
      }
    }
  }

  // Always return success — don't reveal whether the email is registered.
  return NextResponse.json({ success: true })
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run tests/api/forgot-password.test.ts
```

Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/api/auth/forgot-password/route.ts tests/api/forgot-password.test.ts
git commit -m "feat: add forgot-password API endpoint"
```

---

### Task 5: `POST /api/auth/reset-password`

**Files:**
- Create: `trading-dashboard/app/api/auth/reset-password/route.ts`
- Test: `trading-dashboard/tests/api/reset-password.test.ts`

- [ ] **Step 1: Write the failing test**

Create `trading-dashboard/tests/api/reset-password.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/lib/prisma', () => ({
  prisma: {
    user: {
      findFirst: vi.fn(),
      update: vi.fn(),
    },
  },
}))

import { prisma } from '@/lib/prisma'
import { POST } from '@/app/api/auth/reset-password/route'
import { hashResetToken } from '@/lib/resetToken'

function makeRequest(body: unknown) {
  return new Request('http://localhost/api/auth/reset-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

describe('POST /api/auth/reset-password', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('rejects requests missing token or password', async () => {
    const res = await POST(makeRequest({ token: 'abc' }))
    const data = await res.json()

    expect(res.status).toBe(400)
    expect(data.error).toBeTruthy()
    expect(prisma.user.findFirst).not.toHaveBeenCalled()
  })

  it('rejects an invalid or expired token', async () => {
    vi.mocked(prisma.user.findFirst).mockResolvedValue(null)

    const res = await POST(makeRequest({ token: 'bad-token', password: 'newpass123' }))
    const data = await res.json()

    expect(res.status).toBe(400)
    expect(data.error).toBe('This reset link is invalid or has expired')
    expect(prisma.user.update).not.toHaveBeenCalled()
  })

  it('updates the password and clears the token for a valid token', async () => {
    vi.mocked(prisma.user.findFirst).mockResolvedValue({ id: 'user-1' } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({} as any)

    const res = await POST(makeRequest({ token: 'good-token', password: 'newpass123' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true })

    expect(prisma.user.findFirst).toHaveBeenCalledWith({
      where: {
        resetTokenHash: hashResetToken('good-token'),
        resetTokenExpiry: { gt: expect.any(Date) },
      },
    })

    const updateArgs = vi.mocked(prisma.user.update).mock.calls[0][0] as any
    expect(updateArgs.where).toEqual({ id: 'user-1' })
    expect(updateArgs.data.resetTokenHash).toBeNull()
    expect(updateArgs.data.resetTokenExpiry).toBeNull()
    expect(typeof updateArgs.data.passwordHash).toBe('string')
    expect(updateArgs.data.passwordHash).not.toBe('newpass123')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run tests/api/reset-password.test.ts
```

Expected: FAIL — `Cannot find module '@/app/api/auth/reset-password/route'`

- [ ] **Step 3: Write the implementation**

Create `trading-dashboard/app/api/auth/reset-password/route.ts`:

```ts
// trading-dashboard/app/api/auth/reset-password/route.ts
import { NextResponse } from 'next/server'
import bcrypt from 'bcryptjs'
import { prisma } from '@/lib/prisma'
import { hashResetToken } from '@/lib/resetToken'

export async function POST(req: Request) {
  const body = await req.json().catch(() => null) as { token?: string; password?: string } | null
  const token = body?.token
  const password = body?.password

  if (!token || !password) {
    return NextResponse.json({ error: 'Token and new password are required' }, { status: 400 })
  }

  const user = await prisma.user.findFirst({
    where: {
      resetTokenHash: hashResetToken(token),
      resetTokenExpiry: { gt: new Date() },
    },
  })

  if (!user) {
    return NextResponse.json({ error: 'This reset link is invalid or has expired' }, { status: 400 })
  }

  const passwordHash = await bcrypt.hash(password, 10)
  await prisma.user.update({
    where: { id: user.id },
    data: { passwordHash, resetTokenHash: null, resetTokenExpiry: null },
  })

  return NextResponse.json({ success: true })
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run tests/api/reset-password.test.ts
```

Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/api/auth/reset-password/route.ts tests/api/reset-password.test.ts
git commit -m "feat: add reset-password API endpoint"
```

---

### Task 6: `/forgot-password` page

**Files:**
- Create: `trading-dashboard/app/forgot-password/page.tsx`

- [ ] **Step 1: Write the page**

Create `trading-dashboard/app/forgot-password/page.tsx`:

```tsx
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
```

- [ ] **Step 2: Type-check**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add app/forgot-password/page.tsx
git commit -m "feat: add forgot-password page"
```

---

### Task 7: `/reset-password` page

**Files:**
- Create: `trading-dashboard/app/reset-password/page.tsx`

- [ ] **Step 1: Write the page**

Create `trading-dashboard/app/reset-password/page.tsx`. `useSearchParams` requires a `Suspense` boundary in the App Router, so the form is split into an inner component:

```tsx
// trading-dashboard/app/reset-password/page.tsx
'use client'
import { Suspense, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { Zap, Eye, EyeOff } from 'lucide-react'

function ResetPasswordForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const token = searchParams.get('token')

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
        body:    JSON.stringify({ token, password }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.error ?? 'Could not reset password')
        return
      }
      router.push('/login?reset=success')
    } catch {
      setError('Something went wrong — please try again')
    } finally {
      setLoading(false)
    }
  }

  if (!token) {
    return (
      <>
        <p className="text-sm text-bear">This reset link is missing its token.</p>
        <Link href="/forgot-password" className="block text-center text-xs text-brand-cyan hover:underline">
          Request a new reset link
        </Link>
      </>
    )
  }

  return (
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
        {loading ? 'Please wait…' : 'Reset password'}
      </button>
    </form>
  )
}

export default function ResetPasswordPage() {
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
        <Suspense fallback={null}>
          <ResetPasswordForm />
        </Suspense>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add app/reset-password/page.tsx
git commit -m "feat: add reset-password page"
```

---

### Task 8: Allow the new pages without a session

**Files:**
- Modify: `trading-dashboard/middleware.ts`

- [ ] **Step 1: Update the middleware**

Replace the contents of `trading-dashboard/middleware.ts`:

```ts
// trading-dashboard/middleware.ts
import { NextResponse } from 'next/server'
import { auth } from '@/auth'

const PUBLIC_PATHS = ['/login', '/forgot-password', '/reset-password']

export default auth((req) => {
  const isLoggedIn = !!req.auth
  const isPublicPage = PUBLIC_PATHS.includes(req.nextUrl.pathname)

  if (!isLoggedIn && !isPublicPage) {
    return NextResponse.redirect(new URL('/login', req.nextUrl))
  }
  if (isLoggedIn && isPublicPage) {
    return NextResponse.redirect(new URL('/', req.nextUrl))
  }
})

export const config = {
  // Protect page routes only; /api/* routes check auth() themselves and return 401.
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico|favicon.svg).*)'],
}
```

- [ ] **Step 2: Type-check**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add middleware.ts
git commit -m "feat: allow forgot/reset password pages without a session"
```

---

### Task 9: Wire up `/login`

**Files:**
- Modify: `trading-dashboard/app/login/page.tsx`

- [ ] **Step 1: Wrap the page in Suspense and read the `reset` query param**

`app/login/page.tsx` currently starts with:

```tsx
// trading-dashboard/app/login/page.tsx
'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { signIn } from 'next-auth/react'
import { Zap, Eye, EyeOff } from 'lucide-react'
```

and ends with:

```tsx
export default function LoginPage() {
  const router = useRouter()
  const [mode, setMode]         = useState<'signin' | 'signup'>('signin')
  ...
  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg-base px-4">
      ...
    </div>
  )
}
```

Change the imports to:

```tsx
// trading-dashboard/app/login/page.tsx
'use client'
import { Suspense, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { signIn } from 'next-auth/react'
import { Zap, Eye, EyeOff } from 'lucide-react'
```

Rename `export default function LoginPage()` to `function LoginForm()`, and add a new default export that wraps it:

```tsx
export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  )
}
```

(`LoginForm` keeps its existing body — just the function name and `export default` move.)

- [ ] **Step 2: Read the `reset` query param**

Inside `LoginForm`, right after the existing state declarations (after `const [showPassword, setShowPassword] = useState(false)`), add:

```tsx
  const searchParams = useSearchParams()
  const resetSuccess = searchParams.get('reset') === 'success'
```

- [ ] **Step 3: Show the success message**

In the JSX, immediately after the closing `</div>` of the logo block (before the `<div className="flex rounded-lg bg-bg-base p-1 text-xs font-medium">` sign-in/create-account toggle), add:

```tsx
        {resetSuccess && (
          <p className="text-xs text-bull">Password updated — sign in with your new password.</p>
        )}
```

- [ ] **Step 4: Add the "Forgot password?" link**

The Password field block is:

```tsx
          <div>
            <label className="text-xs text-muted">Password</label>
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
```

Immediately after this `</div>` (the one closing the Password field block), add:

```tsx
          {mode === 'signin' && (
            <Link href="/forgot-password" className="block text-right text-xs text-brand-cyan hover:underline">
              Forgot password?
            </Link>
          )}
```

- [ ] **Step 5: Type-check**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add app/login/page.tsx
git commit -m "feat: add forgot-password link and reset-success message to login"
```

---

### Task 10: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
cd trading-dashboard && npm test
```

Expected: all tests pass, including the 6 new tests from Tasks 2, 4, 5.

- [ ] **Step 2: Type-check**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Manual smoke test**

Start the dev server (`npm run dev`), then:

1. Visit `/login`, click "Forgot password?" → lands on `/forgot-password`.
2. Submit an email that belongs to a real test user → see the generic "If an account exists..." message.
3. Check the inbox for that email → a "Reset your TradingBot password" email from `ttradingbott@gmail.com` should arrive with a link to `/reset-password?token=...`.
   - If Brevo rejects the send (e.g. sender not fully verified yet), the server console will log `Failed to send password reset email` with the Brevo error — note this for follow-up, but the UI still shows the generic success message.
4. Open the link → `/reset-password` shows the new-password form.
5. Enter a new password (and matching confirm), submit → redirected to `/login?reset=success`, showing "Password updated — sign in with your new password."
6. Sign in with the new password → succeeds.
7. Re-visit the same `/reset-password?token=...` link again → submitting now shows "This reset link is invalid or has expired" (token was cleared after first use).
8. Visit `/forgot-password` or `/reset-password?token=anything` while already signed in → redirected to `/`.

- [ ] **Step 4: Commit any final fixups**

If smoke testing reveals small issues, fix them and commit:

```bash
git add -A
git commit -m "fix: address smoke-test findings for password reset flow"
```
