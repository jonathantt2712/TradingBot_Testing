# Profile Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/profile` page where users can view their email/member-since date, edit their phone number, and change their password via a modal; remove the always-visible Change Password card from Settings; add a Profile link to navigation.

**Architecture:** Server component `/profile` page fetches `email`, `phone`, `createdAt` from `prisma.user` and renders a header card plus two client-component cards: `AccountDetailsCard` (phone edit, calls new `PATCH /api/auth/profile`) and `SecurityCard` (button opening `ChangePasswordModal`, which calls the existing `POST /api/auth/change-password`). A shared `ProfileCard` wrapper matches the Settings page's card styling. Navigation gets a new "Profile" entry in the Sidebar footer and MobileNav.

**Tech Stack:** Next.js 14 App Router, TypeScript, Prisma, NextAuth v5, Vitest, lucide-react, sonner (toasts)

---

## Reference: spec

See `docs/superpowers/specs/2026-06-14-profile-page-design.md` for the approved design.

## File Structure

- Create: `trading-dashboard/app/api/auth/profile/route.ts` — `PATCH` handler for phone updates
- Create: `trading-dashboard/tests/api/profile.test.ts` — tests for the route above
- Create: `trading-dashboard/components/profile/ProfileCard.tsx` — shared card wrapper (title/icon/header row)
- Create: `trading-dashboard/components/profile/ChangePasswordModal.tsx` — modal version of the change-password form
- Create: `trading-dashboard/components/profile/AccountDetailsCard.tsx` — editable phone field
- Create: `trading-dashboard/components/profile/SecurityCard.tsx` — "Change Password" button + modal trigger
- Create: `trading-dashboard/app/profile/page.tsx` — server component assembling the three cards
- Modify: `trading-dashboard/app/settings/page.tsx` — remove `ChangePasswordCard` and its now-unused imports
- Modify: `trading-dashboard/components/layout/Sidebar.tsx` — add "Profile" link (Sidebar footer) and "Profile" tab (MobileNav)

---

### Task 1: `PATCH /api/auth/profile` route

**Files:**
- Create: `trading-dashboard/app/api/auth/profile/route.ts`
- Test: `trading-dashboard/tests/api/profile.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `trading-dashboard/tests/api/profile.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/auth', () => ({
  auth: vi.fn(),
}))
vi.mock('@/lib/prisma', () => ({
  prisma: {
    user: {
      update: vi.fn(),
    },
  },
}))

import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'
import { PATCH } from '@/app/api/auth/profile/route'

function makeRequest(body: unknown) {
  return new Request('http://localhost/api/auth/profile', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

describe('PATCH /api/auth/profile', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('rejects unauthenticated requests', async () => {
    vi.mocked(auth).mockResolvedValue(null as any)

    const res = await PATCH(makeRequest({ phone: '+972501234567' }))
    const data = await res.json()

    expect(res.status).toBe(401)
    expect(data.error).toBeTruthy()
    expect(prisma.user.update).not.toHaveBeenCalled()
  })

  it('rejects an invalid phone number', async () => {
    vi.mocked(auth).mockResolvedValue({ user: { id: 'user-1' } } as any)

    const res = await PATCH(makeRequest({ phone: 'not-a-phone' }))
    const data = await res.json()

    expect(res.status).toBe(400)
    expect(data.error).toBeTruthy()
    expect(prisma.user.update).not.toHaveBeenCalled()
  })

  it('clears the phone number when given an empty string', async () => {
    vi.mocked(auth).mockResolvedValue({ user: { id: 'user-1' } } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({ phone: null } as any)

    const res = await PATCH(makeRequest({ phone: '' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true, phone: null })

    const updateArgs = vi.mocked(prisma.user.update).mock.calls[0][0] as any
    expect(updateArgs.where).toEqual({ id: 'user-1' })
    expect(updateArgs.data).toEqual({ phone: null })
  })

  it('updates the phone number when valid', async () => {
    vi.mocked(auth).mockResolvedValue({ user: { id: 'user-1' } } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({ phone: '+972501234567' } as any)

    const res = await PATCH(makeRequest({ phone: '+972501234567' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true, phone: '+972501234567' })

    const updateArgs = vi.mocked(prisma.user.update).mock.calls[0][0] as any
    expect(updateArgs.where).toEqual({ id: 'user-1' })
    expect(updateArgs.data).toEqual({ phone: '+972501234567' })
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `trading-dashboard/`): `npx vitest run tests/api/profile.test.ts`
Expected: FAIL — `Cannot find module '@/app/api/auth/profile/route'`

- [ ] **Step 3: Write the route implementation**

Create `trading-dashboard/app/api/auth/profile/route.ts`:

```ts
// trading-dashboard/app/api/auth/profile/route.ts
import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'

const PHONE_REGEX = /^\+?\d{6,15}$/

export async function PATCH(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const body = await req.json().catch(() => null) as { phone?: string } | null
  const phone = body?.phone

  if (phone === undefined) {
    return NextResponse.json({ error: 'Phone is required' }, { status: 400 })
  }

  if (phone !== '' && !PHONE_REGEX.test(phone)) {
    return NextResponse.json({ error: 'Enter a valid phone number' }, { status: 400 })
  }

  const updated = await prisma.user.update({
    where: { id: session.user.id },
    data: { phone: phone === '' ? null : phone },
  })

  return NextResponse.json({ success: true, phone: updated.phone })
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run tests/api/profile.test.ts`
Expected: PASS (4/4)

- [ ] **Step 5: Commit**

```bash
git add trading-dashboard/app/api/auth/profile/route.ts trading-dashboard/tests/api/profile.test.ts
git commit -m "feat: add PATCH /api/auth/profile for phone updates"
```

---

### Task 2: `ProfileCard` shared wrapper

**Files:**
- Create: `trading-dashboard/components/profile/ProfileCard.tsx`

- [ ] **Step 1: Write the component**

Create `trading-dashboard/components/profile/ProfileCard.tsx`:

```tsx
import { cn } from '@/lib/utils'

interface ProfileCardProps {
  title: string
  icon: React.ElementType
  iconColor: string
  children: React.ReactNode
}

export function ProfileCard({ title, icon: Icon, iconColor, children }: ProfileCardProps) {
  return (
    <div className="card p-5 space-y-4">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-bg-hover">
          <Icon className={cn('h-4 w-4', iconColor)} />
        </div>
        <h2 className="text-sm font-semibold text-primary">{title}</h2>
      </div>
      {children}
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run (from `trading-dashboard/`): `npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add trading-dashboard/components/profile/ProfileCard.tsx
git commit -m "feat: add ProfileCard wrapper for profile page"
```

---

### Task 3: `ChangePasswordModal` component

**Files:**
- Create: `trading-dashboard/components/profile/ChangePasswordModal.tsx`

- [ ] **Step 1: Write the component**

Create `trading-dashboard/components/profile/ChangePasswordModal.tsx`:

```tsx
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
```

- [ ] **Step 2: Type-check**

Run (from `trading-dashboard/`): `npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add trading-dashboard/components/profile/ChangePasswordModal.tsx
git commit -m "feat: add ChangePasswordModal component"
```

---

### Task 4: `AccountDetailsCard` component

**Files:**
- Create: `trading-dashboard/components/profile/AccountDetailsCard.tsx`

- [ ] **Step 1: Write the component**

Create `trading-dashboard/components/profile/AccountDetailsCard.tsx`:

```tsx
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
        <button onClick={save} disabled={saving} className="btn-primary text-xs disabled:opacity-50">
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </ProfileCard>
  )
}
```

- [ ] **Step 2: Type-check**

Run (from `trading-dashboard/`): `npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add trading-dashboard/components/profile/AccountDetailsCard.tsx
git commit -m "feat: add AccountDetailsCard component for phone editing"
```

---

### Task 5: `SecurityCard` component

**Files:**
- Create: `trading-dashboard/components/profile/SecurityCard.tsx`

- [ ] **Step 1: Write the component**

Create `trading-dashboard/components/profile/SecurityCard.tsx`:

```tsx
'use client'
import { useState } from 'react'
import { Lock } from 'lucide-react'
import { ProfileCard } from './ProfileCard'
import { ChangePasswordModal } from './ChangePasswordModal'

export function SecurityCard() {
  const [open, setOpen] = useState(false)

  return (
    <ProfileCard title="Security" icon={Lock} iconColor="text-brand-purple">
      <button onClick={() => setOpen(true)} className="btn-primary text-xs">
        Change Password
      </button>
      {open && <ChangePasswordModal onClose={() => setOpen(false)} />}
    </ProfileCard>
  )
}
```

- [ ] **Step 2: Type-check**

Run (from `trading-dashboard/`): `npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add trading-dashboard/components/profile/SecurityCard.tsx
git commit -m "feat: add SecurityCard component with change-password modal trigger"
```

---

### Task 6: `/profile` page

**Files:**
- Create: `trading-dashboard/app/profile/page.tsx`

- [ ] **Step 1: Write the page**

Create `trading-dashboard/app/profile/page.tsx`:

```tsx
import { redirect } from 'next/navigation'
import { User } from 'lucide-react'
import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'
import { ProfileCard } from '@/components/profile/ProfileCard'
import { AccountDetailsCard } from '@/components/profile/AccountDetailsCard'
import { SecurityCard } from '@/components/profile/SecurityCard'

export default async function ProfilePage() {
  const session = await auth()
  if (!session?.user?.id) redirect('/login')

  const user = await prisma.user.findUnique({
    where: { id: session.user.id },
    select: { email: true, phone: true, createdAt: true },
  })
  if (!user) redirect('/login')

  const memberSince = user.createdAt.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 md:space-y-6 max-w-[900px]">
      <div>
        <h1 className="text-xl font-bold text-primary">Profile</h1>
        <p className="text-xs text-muted mt-0.5">Account details and security</p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ProfileCard title="Account" icon={User} iconColor="text-brand-cyan">
          <div className="flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-brand-cyan/10 border border-brand-cyan/30 text-lg font-semibold text-brand-cyan">
              {user.email.charAt(0).toUpperCase()}
            </div>
            <div>
              <p className="text-sm font-medium text-primary">{user.email}</p>
              <p className="text-[11px] text-muted">Contact support to change your email</p>
            </div>
          </div>
          <p className="text-xs text-subtle">Member since {memberSince}</p>
        </ProfileCard>

        <AccountDetailsCard initialPhone={user.phone ?? ''} />

        <SecurityCard />
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run (from `trading-dashboard/`): `npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add trading-dashboard/app/profile/page.tsx
git commit -m "feat: add /profile page"
```

---

### Task 7: Remove `ChangePasswordCard` from Settings page

**Files:**
- Modify: `trading-dashboard/app/settings/page.tsx`

- [ ] **Step 1: Remove the `Lock, Eye, EyeOff` icon imports**

In `trading-dashboard/app/settings/page.tsx`, change the lucide-react import (around line 3-6) from:

```tsx
import {
  CheckCircle2, XCircle, Loader2, ExternalLink,
  Key, Server, Zap, Shield, RefreshCw, Activity, Lock, Eye, EyeOff,
} from 'lucide-react'
```

to:

```tsx
import {
  CheckCircle2, XCircle, Loader2, ExternalLink,
  Key, Server, Zap, Shield, RefreshCw, Activity,
} from 'lucide-react'
```

- [ ] **Step 2: Remove the `ChangePasswordCard` function**

Delete the entire `ChangePasswordCard` function (from `function ChangePasswordCard() {` through its closing `}`, located between `AlpacaAccountCard` and `SettingsPage`).

- [ ] **Step 3: Remove the `<ChangePasswordCard />` render call**

In the `SettingsPage` component's grid, remove this line:

```tsx
        <ChangePasswordCard />

```

(leaving `<AlpacaAccountCard />` followed directly by the `{/* Alpaca */}` comment block).

- [ ] **Step 4: Type-check**

Run (from `trading-dashboard/`): `npx tsc --noEmit`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add trading-dashboard/app/settings/page.tsx
git commit -m "refactor: move change-password UI from Settings to Profile page"
```

---

### Task 8: Navigation — add "Profile" link to Sidebar and MobileNav

**Files:**
- Modify: `trading-dashboard/components/layout/Sidebar.tsx`

- [ ] **Step 1: Import the `User` icon**

Change the lucide-react import (line 5-8) from:

```tsx
import {
  LayoutDashboard, TrendingUp, History, BarChart2,
  Zap, Settings, ExternalLink, FlaskConical, LogOut,
} from 'lucide-react'
```

to:

```tsx
import {
  LayoutDashboard, TrendingUp, History, BarChart2,
  Zap, Settings, ExternalLink, FlaskConical, LogOut, User,
} from 'lucide-react'
```

- [ ] **Step 2: Add a "Profile" tab to `MobileNav`, next to "Settings"**

In `MobileNav`, change:

```tsx
      <Link
        href="/settings"
        className={cn(
          'flex flex-col items-center gap-0.5 px-2 py-2 rounded-lg text-[10px] font-medium transition-colors min-w-0',
          path === '/settings' ? 'text-brand-cyan' : 'text-muted hover:text-primary',
        )}
      >
        <Settings className="h-5 w-5 shrink-0" />
        <span>Settings</span>
      </Link>
    </nav>
```

to:

```tsx
      <Link
        href="/profile"
        className={cn(
          'flex flex-col items-center gap-0.5 px-2 py-2 rounded-lg text-[10px] font-medium transition-colors min-w-0',
          path === '/profile' ? 'text-brand-cyan' : 'text-muted hover:text-primary',
        )}
      >
        <User className="h-5 w-5 shrink-0" />
        <span>Profile</span>
      </Link>
      <Link
        href="/settings"
        className={cn(
          'flex flex-col items-center gap-0.5 px-2 py-2 rounded-lg text-[10px] font-medium transition-colors min-w-0',
          path === '/settings' ? 'text-brand-cyan' : 'text-muted hover:text-primary',
        )}
      >
        <Settings className="h-5 w-5 shrink-0" />
        <span>Settings</span>
      </Link>
    </nav>
```

- [ ] **Step 3: Replace the plain-text email in the Sidebar footer with a "Profile" link**

In `Sidebar`, change:

```tsx
      <div className="border-t border-bg-border px-3 py-3 space-y-0.5">
        {email && (
          <div className="px-3 py-2 text-xs text-muted truncate" title={email}>
            {email}
          </div>
        )}
        <Link
          href="/settings"
```

to:

```tsx
      <div className="border-t border-bg-border px-3 py-3 space-y-0.5">
        {email && (
          <Link
            href="/profile"
            className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-subtle hover:bg-bg-hover hover:text-primary transition-colors"
            title={email}
          >
            <User className="h-4 w-4 shrink-0" />
            <span className="truncate">{email}</span>
          </Link>
        )}
        <Link
          href="/settings"
```

- [ ] **Step 4: Type-check**

Run (from `trading-dashboard/`): `npx tsc --noEmit`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add trading-dashboard/components/layout/Sidebar.tsx
git commit -m "feat: add Profile link to Sidebar and MobileNav"
```

---

### Task 9: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run (from `trading-dashboard/`): `npx vitest run`
Expected: all tests pass (including the existing `change-password.test.ts` and the new `profile.test.ts`)

- [ ] **Step 2: Type-check the whole project**

Run (from `trading-dashboard/`): `npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Manual/Playwright smoke test**

With the dev server running (`npm run dev`):
1. Sign in, then click the email/"Profile" link in the Sidebar (or the "Profile" tab on mobile width) — confirm it navigates to `/profile`.
2. Confirm the Account card shows the correct avatar initial, email, "Contact support to change your email" note, and "Member since <Month YYYY>".
3. Edit the phone number in the Account Details card, click "Save", confirm a success toast and the value persists after a page reload.
4. Click "Change Password" in the Security card, confirm the modal opens with the overlay/blur styling, fill in current/new/confirm password, submit, confirm success toast and modal closes.
5. Navigate to `/settings` and confirm the "Change Password" card is gone and the rest of the page renders unchanged.

- [ ] **Step 4: Final commit (if any fixes were needed)**

```bash
git add -A
git commit -m "fix: address issues found during profile page verification"
```

(Skip this step if no fixes were needed.)
