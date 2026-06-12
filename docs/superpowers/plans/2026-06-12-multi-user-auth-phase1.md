# Multi-user Auth + Per-User Alpaca Trading (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a login/signup page that gates the whole dashboard. Each user signs up with email/password + their own Alpaca API keys (Key ID, Secret, Paper/Live); after sign-in, every per-account view (account, positions, history, P&L) and the "Execute" action operate on that user's own Alpaca account. Sessions expire after 30 minutes of inactivity.

**Architecture:** Postgres (via Prisma) stores `User` rows with bcrypt password hashes and AES-256-GCM-encrypted Alpaca credentials. Auth.js v5 (Credentials provider, JWT session) decrypts the credentials once at login and carries them in the session token, so `lib/alpaca.ts` functions take an explicit `creds: AlpacaCreds` argument built from `session.alpaca` on every request — no per-request DB lookups. `middleware.ts` redirects unauthenticated page loads to `/login`; API routes independently return 401 if there's no session. The Python bot and its `/api/bot/*` recommendations are untouched (shared account, Phase 2 future work).

**Tech Stack:** Next.js 14 App Router, TypeScript, `next-auth@5.0.0-beta.31` (Auth.js v5), `@prisma/client` + `prisma` 7, Postgres (Vercel Postgres/Neon), `bcryptjs`, Node's built-in `crypto` (AES-256-GCM), Vitest (new — for `lib/crypto.ts` unit tests).

---

## File structure overview

**New files:**
- `vitest.config.ts` — minimal Vitest setup (first test framework in this project)
- `lib/crypto.ts` + `tests/lib/crypto.test.ts` — AES-256-GCM encrypt/decrypt for credentials at rest
- `prisma/schema.prisma` — `User` model
- `lib/prisma.ts` — Prisma client singleton
- `auth.ts` — Auth.js v5 config (Credentials provider, JWT callbacks)
- `types/next-auth.d.ts` — session/JWT type augmentation
- `app/api/auth/[...nextauth]/route.ts` — Auth.js route handlers
- `lib/session.ts` — `getAlpacaCreds()` helper used by API routes
- `middleware.ts` — route protection
- `app/api/auth/signup/route.ts` — create user (validates Alpaca creds first)
- `app/login/page.tsx` — sign-in / sign-up UI
- `app/api/alpaca/settings/route.ts` — view/update stored Alpaca credentials

**Modified files:**
- `lib/alpaca.ts` — every exported function takes `creds: AlpacaCreds`
- `app/api/alpaca/account/route.ts`, `orders/route.ts`, `positions/route.ts`, `snapshots/route.ts`, `positions/[symbol]/route.ts`, `bars/route.ts` — read creds from session
- `app/api/bot/execute/route.ts`, `bot/history/route.ts`, `bot/stats/route.ts` — read creds from session
- `app/page.tsx`, `app/history/page.tsx`, `app/pnl/page.tsx` — read creds from session
- `app/layout.tsx`, `components/layout/Sidebar.tsx` — show user email + log out
- `app/settings/page.tsx` — new "Alpaca Account" card
- `package.json` — new deps + `test` script
- `.env.example` — `DATABASE_URL`, `ENCRYPTION_KEY`, `AUTH_SECRET`

---

### Task 1: Add Vitest

**Files:**
- Modify: `trading-dashboard/package.json`
- Create: `trading-dashboard/vitest.config.ts`

- [ ] **Step 1: Install Vitest**

Run:
```bash
cd trading-dashboard
npm install -D vitest
```
Expected: `vitest` added under `devDependencies` in `package.json`.

- [ ] **Step 2: Add the `test` script**

In `trading-dashboard/package.json`, add to `"scripts"`:
```json
"test": "vitest run"
```

- [ ] **Step 3: Create `vitest.config.ts`**

```ts
import { defineConfig } from 'vitest/config'
import path from 'path'

export default defineConfig({
  test: {
    environment: 'node',
    env: {
      ENCRYPTION_KEY: 'iI+WjblZstJJlVjNt0D1zQpRKrDy1c7UlycDo0himPU=',
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
})
```

- [ ] **Step 4: Run the (empty) test suite to confirm Vitest works**

Run: `npm test`
Expected: `No test files found` (exit code 1 is fine — there are no tests yet; this just confirms Vitest runs).

- [ ] **Step 5: Commit**

```bash
git add package.json package-lock.json vitest.config.ts
git commit -m "chore: add vitest test runner"
```

---

### Task 2: `lib/crypto.ts` — AES-256-GCM encrypt/decrypt

**Files:**
- Create: `trading-dashboard/lib/crypto.ts`
- Test: `trading-dashboard/tests/lib/crypto.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// trading-dashboard/tests/lib/crypto.test.ts
import { describe, it, expect } from 'vitest'
import { encrypt, decrypt } from '@/lib/crypto'

describe('lib/crypto', () => {
  it('round-trips a string through encrypt/decrypt', () => {
    const plaintext = 'PKBDDZ2MMKE6P2JREVXEVTBLZ3'
    const ciphertext = encrypt(plaintext)
    expect(ciphertext).not.toBe(plaintext)
    expect(decrypt(ciphertext)).toBe(plaintext)
  })

  it('produces different ciphertext for the same input each time', () => {
    const plaintext = 'same-secret'
    expect(encrypt(plaintext)).not.toBe(encrypt(plaintext))
  })

  it('throws if ENCRYPTION_KEY is missing', () => {
    const original = process.env.ENCRYPTION_KEY
    delete process.env.ENCRYPTION_KEY
    expect(() => encrypt('x')).toThrow()
    process.env.ENCRYPTION_KEY = original
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — `Failed to resolve import "@/lib/crypto"`.

- [ ] **Step 3: Implement `lib/crypto.ts`**

```ts
// trading-dashboard/lib/crypto.ts
import crypto from 'crypto'

const ALGORITHM = 'aes-256-gcm'
const IV_LENGTH = 12
const AUTH_TAG_LENGTH = 16

function getKey(): Buffer {
  const key = process.env.ENCRYPTION_KEY
  if (!key) throw new Error('ENCRYPTION_KEY is not set')
  const buf = Buffer.from(key, 'base64')
  if (buf.length !== 32) {
    throw new Error('ENCRYPTION_KEY must be a base64-encoded 32-byte key')
  }
  return buf
}

/** Encrypts a UTF-8 string, returning a base64 string of iv||authTag||ciphertext. */
export function encrypt(plaintext: string): string {
  const iv = crypto.randomBytes(IV_LENGTH)
  const cipher = crypto.createCipheriv(ALGORITHM, getKey(), iv)
  const encrypted = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()])
  const authTag = cipher.getAuthTag()
  return Buffer.concat([iv, authTag, encrypted]).toString('base64')
}

/** Decrypts a base64 string produced by encrypt(). */
export function decrypt(payload: string): string {
  const data = Buffer.from(payload, 'base64')
  const iv = data.subarray(0, IV_LENGTH)
  const authTag = data.subarray(IV_LENGTH, IV_LENGTH + AUTH_TAG_LENGTH)
  const encrypted = data.subarray(IV_LENGTH + AUTH_TAG_LENGTH)
  const decipher = crypto.createDecipheriv(ALGORITHM, getKey(), iv)
  decipher.setAuthTag(authTag)
  return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString('utf8')
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test`
Expected: PASS — 3 tests in `tests/lib/crypto.test.ts`.

- [ ] **Step 5: Commit**

```bash
git add lib/crypto.ts tests/lib/crypto.test.ts
git commit -m "feat: add AES-256-GCM credential encryption helper"
```

---

### Task 3: Prisma schema + client

**Files:**
- Create: `trading-dashboard/prisma/schema.prisma`
- Create: `trading-dashboard/lib/prisma.ts`
- Modify: `trading-dashboard/package.json`
- Modify: `trading-dashboard/.env.example`

- [ ] **Step 1: Install Prisma**

Run:
```bash
cd trading-dashboard
npm install @prisma/client
npm install -D prisma
```

- [ ] **Step 2: Create `prisma/schema.prisma`**

```prisma
generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

model User {
  id           String   @id @default(uuid())
  email        String   @unique
  passwordHash String
  alpacaKeyId  String
  alpacaSecret String
  alpacaPaper  Boolean  @default(true)
  createdAt    DateTime @default(now())
}
```

- [ ] **Step 3: Create `lib/prisma.ts`**

```ts
// trading-dashboard/lib/prisma.ts
import { PrismaClient } from '@prisma/client'

const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient }

export const prisma = globalForPrisma.prisma ?? new PrismaClient()

if (process.env.NODE_ENV !== 'production') {
  globalForPrisma.prisma = prisma
}
```

- [ ] **Step 4: Add `DATABASE_URL` to `.env.example`**

Add to `trading-dashboard/.env.example`:
```
# ── Database (Postgres — Vercel Postgres / Neon) ────────────────────────────
DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require
```

- [ ] **Step 5: Generate the Prisma client**

Run: `npx prisma generate`
Expected: `Generated Prisma Client ... to ./node_modules/@prisma/client`

- [ ] **Step 6: Commit**

```bash
git add prisma/schema.prisma lib/prisma.ts package.json package-lock.json .env.example
git commit -m "feat: add Prisma schema and client for User accounts"
```

> **Note for the engineer running this plan:** `prisma migrate dev` requires a real `DATABASE_URL` (e.g. a Neon connection string) in `.env`. Once you have one, run `npx prisma migrate dev --name init` to create the `User` table before testing signup in Task 7.

---

### Task 4: Auth.js v5 dependencies + session types

**Files:**
- Modify: `trading-dashboard/package.json`
- Create: `trading-dashboard/types/next-auth.d.ts`
- Modify: `trading-dashboard/.env.example`

- [ ] **Step 1: Install Auth.js v5 and bcryptjs**

Run:
```bash
cd trading-dashboard
npm install next-auth@beta bcryptjs
npm install -D @types/bcryptjs
```

- [ ] **Step 2: Create the session/JWT type augmentation**

```ts
// trading-dashboard/types/next-auth.d.ts
import type { DefaultSession } from 'next-auth'

declare module 'next-auth' {
  interface Session {
    user: {
      id: string
    } & DefaultSession['user']
    alpaca: {
      keyId:  string
      secret: string
      paper:  boolean
    }
  }

  interface User {
    alpacaKeyId:  string
    alpacaSecret: string
    alpacaPaper:  boolean
  }
}

declare module 'next-auth/jwt' {
  interface JWT {
    userId: string
    alpaca: {
      keyId:  string
      secret: string
      paper:  boolean
    }
  }
}
```

- [ ] **Step 3: Add `AUTH_SECRET` and `ENCRYPTION_KEY` to `.env.example`**

Add to `trading-dashboard/.env.example`:
```
# ── Auth.js session signing secret (generate with: node -e "console.log(require('crypto').randomBytes(32).toString('base64'))") ──
AUTH_SECRET=

# ── Credential encryption key, base64-encoded 32 bytes (generate the same way as AUTH_SECRET) ──
ENCRYPTION_KEY=
```

- [ ] **Step 4: Generate real values for local `.env.local`**

Run twice (once for each var) and paste the output into `trading-dashboard/.env.local`:
```bash
node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"
```
Add the two resulting lines to `.env.local`:
```
AUTH_SECRET=<first generated value>
ENCRYPTION_KEY=<second generated value>
```
`.env.local` is gitignored — do not commit it.

- [ ] **Step 5: Commit**

```bash
git add package.json package-lock.json types/next-auth.d.ts .env.example
git commit -m "feat: add Auth.js v5 + bcryptjs, session type augmentation"
```

---

### Task 5: `auth.ts` — Auth.js configuration

**Files:**
- Create: `trading-dashboard/auth.ts`

- [ ] **Step 1: Write `auth.ts`**

```ts
// trading-dashboard/auth.ts
import NextAuth from 'next-auth'
import Credentials from 'next-auth/providers/credentials'
import bcrypt from 'bcryptjs'
import { prisma } from '@/lib/prisma'
import { decrypt } from '@/lib/crypto'

export const { handlers, auth, signIn, signOut } = NextAuth({
  session: {
    strategy:  'jwt',
    maxAge:    30 * 60, // 30 minutes
    updateAge: 5 * 60,  // refresh the cookie every 5 minutes of activity
  },
  pages: {
    signIn: '/login',
  },
  providers: [
    Credentials({
      credentials: {
        email:    { label: 'Email',    type: 'email' },
        password: { label: 'Password', type: 'password' },
      },
      async authorize(credentials) {
        const email    = credentials?.email as string | undefined
        const password = credentials?.password as string | undefined
        if (!email || !password) return null

        const user = await prisma.user.findUnique({ where: { email } })
        if (!user) return null

        const valid = await bcrypt.compare(password, user.passwordHash)
        if (!valid) return null

        return {
          id:           user.id,
          email:        user.email,
          alpacaKeyId:  decrypt(user.alpacaKeyId),
          alpacaSecret: decrypt(user.alpacaSecret),
          alpacaPaper:  user.alpacaPaper,
        }
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.userId = user.id as string
        token.alpaca = {
          keyId:  (user as unknown as { alpacaKeyId: string }).alpacaKeyId,
          secret: (user as unknown as { alpacaSecret: string }).alpacaSecret,
          paper:  (user as unknown as { alpacaPaper: boolean }).alpacaPaper,
        }
      }
      return token
    },
    async session({ session, token }) {
      session.user.id = token.userId
      session.alpaca  = token.alpaca
      return session
    },
  },
})
```

- [ ] **Step 2: Verify the project still type-checks**

Run: `npx tsc --noEmit`
Expected: no new errors referencing `auth.ts` (errors about missing `app/api/auth/[...nextauth]/route.ts` not importing it yet are fine — that's Task 6).

- [ ] **Step 3: Commit**

```bash
git add auth.ts
git commit -m "feat: configure Auth.js v5 credentials provider"
```

---

### Task 6: Auth route handler + middleware + creds helper

**Files:**
- Create: `trading-dashboard/app/api/auth/[...nextauth]/route.ts`
- Create: `trading-dashboard/middleware.ts`
- Create: `trading-dashboard/lib/session.ts`

- [ ] **Step 1: Create the Auth.js route handler**

```ts
// trading-dashboard/app/api/auth/[...nextauth]/route.ts
import { handlers } from '@/auth'

export const { GET, POST } = handlers
```

- [ ] **Step 2: Create `middleware.ts`**

```ts
// trading-dashboard/middleware.ts
import { NextResponse } from 'next/server'
import { auth } from '@/auth'

export default auth((req) => {
  const isLoggedIn = !!req.auth
  const isLoginPage = req.nextUrl.pathname === '/login'

  if (!isLoggedIn && !isLoginPage) {
    return NextResponse.redirect(new URL('/login', req.nextUrl))
  }
  if (isLoggedIn && isLoginPage) {
    return NextResponse.redirect(new URL('/', req.nextUrl))
  }
})

export const config = {
  // Protect page routes only; /api/* routes check auth() themselves and return 401.
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico|favicon.svg).*)'],
}
```

- [ ] **Step 3: Create `lib/session.ts`**

```ts
// trading-dashboard/lib/session.ts
import { auth } from '@/auth'
import type { AlpacaCreds } from '@/lib/alpaca'

/** Returns the signed-in user's Alpaca credentials, or null if unauthenticated. */
export async function getAlpacaCreds(): Promise<AlpacaCreds | null> {
  const session = await auth()
  if (!session?.alpaca) return null
  return session.alpaca
}
```

Note: `AlpacaCreds` doesn't exist yet — it's added in Task 9. This file will not type-check until then; that's expected since Task 9 runs immediately after.

- [ ] **Step 4: Commit**

```bash
git add "app/api/auth/[...nextauth]/route.ts" middleware.ts lib/session.ts
git commit -m "feat: add auth route handler, middleware, and session creds helper"
```

---

### Task 7: Refactor `lib/alpaca.ts` to accept per-user credentials

**Files:**
- Modify: `trading-dashboard/lib/alpaca.ts`

- [ ] **Step 1: Rewrite `lib/alpaca.ts`**

Replace the entire file with:

```ts
/**
 * Server-side Alpaca REST client.
 * Never import this from client components -- keys stay on the server.
 */

export interface AlpacaCreds {
  keyId:  string
  secret: string
  paper:  boolean
}

const DATA_BASE = 'https://data.alpaca.markets'

function brokerBase(creds: AlpacaCreds): string {
  return creds.paper
    ? 'https://paper-api.alpaca.markets'
    : 'https://api.alpaca.markets'
}

function headers(creds: AlpacaCreds) {
  return {
    'APCA-API-KEY-ID':     creds.keyId,
    'APCA-API-SECRET-KEY': creds.secret,
    'Content-Type':        'application/json',
  }
}

async function alpacaGet<T>(base: string, path: string, creds: AlpacaCreds, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${base}${path}`, {
    headers: headers(creds),
    next: { revalidate: 10 },
    ...opts,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`Alpaca ${path} -> ${res.status}: ${text}`)
  }
  return res.json()
}

// Account

export interface AlpacaAccount {
  id:                  string
  status:              string
  currency:            string
  buying_power:        string
  cash:                string
  portfolio_value:     string
  equity:              string
  last_equity:         string
  unrealized_pl:       string
  unrealized_plpc:     string
  realized_pl:         string
  daytrade_count:      number
  pattern_day_trader:  boolean
}

export function getAccount(creds: AlpacaCreds): Promise<AlpacaAccount> {
  return alpacaGet(brokerBase(creds), '/v2/account', creds)
}

// Positions

export interface AlpacaPosition {
  symbol:           string
  qty:              string
  side:             string
  avg_entry_price:  string
  current_price:    string
  market_value:     string
  unrealized_pl:    string
  unrealized_plpc:  string
  change_today:     string
}

export function getPositions(creds: AlpacaCreds): Promise<AlpacaPosition[]> {
  return alpacaGet(brokerBase(creds), '/v2/positions', creds, { cache: 'no-store' })
}

// Orders

export interface AlpacaOrder {
  id:               string
  symbol:           string
  side:             string
  qty:              string
  filled_qty:       string
  filled_avg_price: string | null
  status:           string
  created_at:       string
  filled_at:        string | null
  type:             string
}

export function getOrders(creds: AlpacaCreds, status = 'closed', limit = 50): Promise<AlpacaOrder[]> {
  return alpacaGet(brokerBase(creds), `/v2/orders?status=${status}&limit=${limit}&direction=desc`, creds)
}

// Latest quote

export interface AlpacaQuote {
  symbol: string
  quote:  { ap: number; bp: number; as: number; bs: number; t: string }
}

export async function getLatestQuote(creds: AlpacaCreds, symbol: string): Promise<AlpacaQuote> {
  const data = await alpacaGet<{ quotes: Record<string, any> }>(
    DATA_BASE, `/v2/stocks/${symbol}/quotes/latest`, creds
  )
  return { symbol, quote: data.quotes?.[symbol] }
}

// Latest bar

export interface AlpacaBar {
  symbol: string
  bar:    { o: number; h: number; l: number; c: number; v: number; t: string }
}

export async function getLatestBar(creds: AlpacaCreds, symbol: string): Promise<AlpacaBar> {
  const data = await alpacaGet<{ bars: Record<string, any> }>(
    DATA_BASE, `/v2/stocks/${symbol}/bars/latest`, creds
  )
  return { symbol, bar: data.bars?.[symbol] }
}

// Multi-ticker snapshot

export interface AlpacaSnapshot {
  symbol:      string
  latestTrade: { p: number; s: number; t: string }
  latestQuote: { ap: number; bp: number }
  dailyBar:    { o: number; h: number; l: number; c: number; v: number }
  prevDailyBar:{ o: number; h: number; l: number; c: number; v: number }
}

export async function getSnapshots(creds: AlpacaCreds, symbols: string[]): Promise<Record<string, AlpacaSnapshot>> {
  const syms = symbols.join(',')
  const data = await alpacaGet<{ snapshots: Record<string, AlpacaSnapshot> }>(
    DATA_BASE, `/v2/stocks/snapshots?symbols=${encodeURIComponent(syms)}`, creds
  )
  return data.snapshots ?? (data as any)
}

// Bars (multi-symbol)

export interface AlpacaBarsResponse {
  bars: Record<string, Array<{ o: number; h: number; l: number; c: number; v: number; t: string }>>
}

export async function getBars(creds: AlpacaCreds, params: URLSearchParams): Promise<AlpacaBarsResponse> {
  return alpacaGet<AlpacaBarsResponse>(DATA_BASE, `/v2/stocks/bars?${params}`, creds, { cache: 'no-store' })
}

// Order submission

export interface BracketOrderRequest {
  symbol:      string
  side:        'buy' | 'sell'
  qty:         number
  stop_loss:   number
  take_profit: number
}

export interface AlpacaOrderResponse {
  id:              string
  client_order_id: string
  symbol:          string
  side:            string
  qty:             string
  status:          string
  created_at:      string
}

export async function submitBracketOrder(creds: AlpacaCreds, req: BracketOrderRequest): Promise<AlpacaOrderResponse> {
  const body = {
    symbol:        req.symbol,
    qty:           String(req.qty),
    side:          req.side,
    type:          'market',
    time_in_force: 'day',
    order_class:   'bracket',
    stop_loss:     { stop_price:  req.stop_loss.toFixed(2) },
    take_profit:   { limit_price: req.take_profit.toFixed(2) },
  }

  const res = await fetch(`${brokerBase(creds)}/v2/orders`, {
    method:  'POST',
    headers: headers(creds),
    body:    JSON.stringify(body),
    cache:   'no-store',
  })

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`Alpaca submit order -> ${res.status}: ${text}`)
  }
  return res.json()
}

// Close a position

export async function closePosition(creds: AlpacaCreds, symbol: string): Promise<AlpacaOrderResponse> {
  const res = await fetch(`${brokerBase(creds)}/v2/positions/${symbol}`, {
    method:  'DELETE',
    headers: headers(creds),
    cache:   'no-store',
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`Alpaca close position ${symbol} -> ${res.status}: ${text}`)
  }
  return res.json()
}
```

Two functions were added (`getBars`, `closePosition` signature carries through) to absorb the ad-hoc credential logic currently duplicated in `app/api/alpaca/bars/route.ts` and `app/api/alpaca/positions/[symbol]/route.ts` (Task 10).

- [ ] **Step 2: Run `npx tsc --noEmit`**

Expected: errors in every importer of `lib/alpaca.ts` (account/orders/positions/snapshots routes, bot routes, page.tsx/history/pnl pages) — these are fixed in Tasks 8–10. `lib/session.ts` should now type-check cleanly.

- [ ] **Step 3: Commit**

```bash
git add lib/alpaca.ts
git commit -m "feat: lib/alpaca.ts takes explicit per-user AlpacaCreds"
```

---

### Task 8: Update `/api/alpaca/*` routes to use session credentials

**Files:**
- Modify: `trading-dashboard/app/api/alpaca/account/route.ts`
- Modify: `trading-dashboard/app/api/alpaca/orders/route.ts`
- Modify: `trading-dashboard/app/api/alpaca/positions/route.ts`
- Modify: `trading-dashboard/app/api/alpaca/snapshots/route.ts`
- Modify: `trading-dashboard/app/api/alpaca/positions/[symbol]/route.ts`
- Modify: `trading-dashboard/app/api/alpaca/bars/route.ts`

- [ ] **Step 1: `app/api/alpaca/account/route.ts`**

```ts
import { NextResponse } from 'next/server'
import { getAccount } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'

export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const account = await getAccount(creds)
    return NextResponse.json(account)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
```

- [ ] **Step 2: `app/api/alpaca/orders/route.ts`**

```ts
import { NextResponse } from 'next/server'
import { getOrders } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'

export async function GET(req: Request) {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const { searchParams } = new URL(req.url)
    const status = searchParams.get('status') ?? 'closed'
    const limit  = parseInt(searchParams.get('limit') ?? '50')
    const orders = await getOrders(creds, status, limit)
    return NextResponse.json(orders)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
```

- [ ] **Step 3: `app/api/alpaca/positions/route.ts`**

```ts
import { NextResponse } from 'next/server'
import { getPositions } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'

export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const positions = await getPositions(creds)
    return NextResponse.json(positions)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
```

- [ ] **Step 4: `app/api/alpaca/snapshots/route.ts`**

```ts
import { NextResponse } from 'next/server'
import { getSnapshots } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'

export async function GET(req: Request) {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const { searchParams } = new URL(req.url)
    const symbols = (searchParams.get('symbols') ?? 'SPY,QQQ,AAPL,NVDA,MSFT')
      .split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
    const snaps = await getSnapshots(creds, symbols)
    return NextResponse.json(snaps)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
```

- [ ] **Step 5: `app/api/alpaca/positions/[symbol]/route.ts`**

```ts
import { NextRequest, NextResponse } from 'next/server'
import { closePosition } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'

export async function DELETE(
  _req: NextRequest,
  { params }: { params: { symbol: string } },
) {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ message: 'Unauthorized' }, { status: 401 })

  const symbol = decodeURIComponent(params.symbol).toUpperCase()

  try {
    const order = await closePosition(creds, symbol)
    return NextResponse.json({ ok: true, symbol, order })
  } catch (err: any) {
    return NextResponse.json({ message: err.message }, { status: 502 })
  }
}
```

Note: the original handler special-cased a 204 response (no body) as `{ ok: true, symbol }`. `closePosition` in `lib/alpaca.ts` calls `res.json()` unconditionally, which throws on an empty 204 body. Alpaca's `DELETE /v2/positions/{symbol}` returns 200 with an order object in normal operation (it submits a liquidating order), so this is consistent with `lib/alpaca.ts`'s existing `closePosition` behavior used elsewhere — no special-casing needed.

- [ ] **Step 6: `app/api/alpaca/bars/route.ts`**

```ts
import { NextRequest, NextResponse } from 'next/server'
import { getBars } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'

export async function GET(req: NextRequest) {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const { searchParams } = new URL(req.url)
  const symbols   = searchParams.get('symbols') ?? ''
  const timeframe = searchParams.get('timeframe') ?? '5Min'
  const start     = searchParams.get('start') ?? ''
  const limit     = searchParams.get('limit') ?? '78'

  if (!symbols) return NextResponse.json({}, { status: 400 })

  const params = new URLSearchParams({ symbols, timeframe, limit })
  if (start) params.set('start', start)

  try {
    const data = await getBars(creds, params)
    return NextResponse.json(data.bars ?? data)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
```

- [ ] **Step 7: Commit**

```bash
git add app/api/alpaca
git commit -m "feat: alpaca routes use signed-in user's Alpaca credentials"
```

---

### Task 9: Update `/api/bot/*` routes that call `lib/alpaca.ts`

**Files:**
- Modify: `trading-dashboard/app/api/bot/execute/route.ts`
- Modify: `trading-dashboard/app/api/bot/history/route.ts`
- Modify: `trading-dashboard/app/api/bot/stats/route.ts`

- [ ] **Step 1: `app/api/bot/execute/route.ts`**

Add the import and read creds at the top of `POST`, and pass `creds` to `submitBracketOrder`:

```ts
/**
 * POST /api/bot/execute
 *
 * Executes a trade recommendation. Strategy (in order):
 *  1. Submit bracket order directly to the signed-in user's Alpaca account
 *  2. Also notify bot server if it happens to be running (for its trade log)
 *  3. If Alpaca is unavailable, return a local paper order ID so UI never breaks
 *
 * This makes the execute flow completely independent of localhost:8000 being up.
 */
import { NextResponse }       from 'next/server'
import { revalidatePath }      from 'next/cache'
import { submitBracketOrder } from '@/lib/alpaca'
import { getAlpacaCreds }     from '@/lib/session'
import { botPost }            from '@/lib/bot-api'
import type { ExecuteRequest, ExecuteResponse } from '@/types/trading'

// ── Idempotency guard ──────────────────────────────────────────────────────
// Reject the same recommendation_id if it arrives again within 30 seconds.
// Prevents double-executions from rapid clicks or network retries.
const _recentIds  = new Map<string, number>() // rec_id -> timestamp ms
const _DEDUP_MS   = 30_000

function _isDuplicate(recId: string | undefined): boolean {
  if (!recId) return false
  const now = Date.now()
  for (const [id, ts] of _recentIds) {
    if (now - ts > _DEDUP_MS) _recentIds.delete(id)
  }
  if (_recentIds.has(recId)) return true
  _recentIds.set(recId, now)
  return false
}

export async function POST(req: Request) {
  const creds = await getAlpacaCreds()
  if (!creds) {
    return NextResponse.json(
      { success: false, order_id: '', message: 'Unauthorized' },
      { status: 401 },
    )
  }

  let body: ExecuteRequest
  try {
    body = await req.json()
  } catch {
    return NextResponse.json(
      { success: false, order_id: '', message: 'Invalid request body' },
      { status: 400 },
    )
  }

  // Idempotency check — 409 if same rec_id arrives within 30s
  if (_isDuplicate(body.recommendation_id)) {
    return NextResponse.json(
      { success: false, order_id: '', message: `Duplicate: '${body.recommendation_id}' already executed within 30s` },
      { status: 409 },
    )
  }

  const { ticker, direction, qty, stop_loss, take_profit } = body

  // -- 1. Submit to the signed-in user's Alpaca account
  let orderId = `PAPER-${Date.now().toString(36).toUpperCase()}`
  let message = ''
  let alpacaSuccess = false

  try {
    const alpacaOrder = await submitBracketOrder(creds, {
      symbol:      ticker,
      side:        direction === 'LONG' ? 'buy' : 'sell',
      qty,
      stop_loss,
      take_profit,
    })
    orderId       = alpacaOrder.id
    message       = `${direction} ${qty}x ${ticker} submitted to Alpaca ${creds.paper ? 'Paper' : 'Live'} (order ${orderId})`
    alpacaSuccess = true
  } catch (err: any) {
    message = `${direction} ${qty}x ${ticker} recorded locally (Alpaca: ${err.message})`
  }

  // -- 2. Notify bot server (best-effort) — include resolved order_id and score
  botPost('/api/execute', {
    ...body,
    order_id: orderId,
    score:    body.composite_score ?? null,
  }).catch(() => {})

  // -- 3. Invalidate dashboard cache so positions refresh on next load
  revalidatePath('/')
  revalidatePath('/history')
  revalidatePath('/pnl')

  // -- 4. Always return success
  return NextResponse.json({
    success:  true,
    order_id: orderId,
    message,
    alpaca:   alpacaSuccess,
  } as ExecuteResponse & { alpaca: boolean })
}
```

- [ ] **Step 2: `app/api/bot/history/route.ts`**

```ts
import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { getOrders } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoHistory } from '@/lib/api'
import type { TradeRecord } from '@/types/trading'

/** Merge bot history with real Alpaca orders for full picture. */
export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    // 1. Bot's persisted trade records
    const botTrades = await botGet<TradeRecord[]>('/api/history').catch(() => [])

    // 2. Real closed orders from Alpaca (fills broker gaps)
    const alpacaOrders = await getOrders(creds, 'closed', 100).catch(() => [])
    const fromAlpaca: TradeRecord[] = alpacaOrders
      .filter(o => o.filled_qty && parseFloat(o.filled_qty) > 0)
      .map(o => ({
        id:        o.id,
        ticker:    o.symbol,
        direction: o.side === 'buy' ? 'LONG' : 'SHORT',
        entry:     parseFloat(o.filled_avg_price ?? '0'),
        exit:      null,
        qty:       parseInt(o.filled_qty),
        pnl:       null,
        pnl_pct:   null,
        opened_at: o.created_at,
        closed_at: o.filled_at,
        duration:  null,
        status:    'closed',
        order_id:  o.id,
      } as TradeRecord))

    // Merge — bot records take precedence (they have P&L calc)
    const botIds = new Set(botTrades.map(t => t.order_id).filter(Boolean))
    const merged = [
      ...botTrades,
      ...fromAlpaca.filter(t => !botIds.has(t.id)),
    ].sort((a, b) => b.opened_at.localeCompare(a.opened_at))

    return NextResponse.json(merged.length ? merged : demoHistory())
  } catch {
    return NextResponse.json(demoHistory())
  }
}
```

- [ ] **Step 3: `app/api/bot/stats/route.ts`**

```ts
import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { getAccount } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoStats } from '@/lib/api'
import type { PortfolioStats } from '@/types/trading'

export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    // Pull stats from bot + enrich with live Alpaca account data
    const [botStats, account] = await Promise.allSettled([
      botGet<PortfolioStats>('/api/stats'),
      getAccount(creds),
    ])

    const stats: PortfolioStats = botStats.status === 'fulfilled'
      ? botStats.value
      : demoStats()

    // Override P&L with live Alpaca equity if available
    if (account.status === 'fulfilled') {
      const acc = account.value
      const totalPnl = parseFloat(acc.unrealized_pl) + parseFloat(acc.realized_pl ?? '0')
      const todayPnl = parseFloat(acc.equity) - parseFloat(acc.last_equity)
      if (!isNaN(totalPnl)) stats.total_pnl = +totalPnl.toFixed(2)
      if (!isNaN(todayPnl)) stats.today_pnl = +todayPnl.toFixed(2)
      stats.open_positions = 0 // will be filled by positions endpoint
    }

    return NextResponse.json(stats)
  } catch {
    return NextResponse.json(demoStats())
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add app/api/bot/execute/route.ts app/api/bot/history/route.ts app/api/bot/stats/route.ts
git commit -m "feat: bot execute/history/stats routes use per-user Alpaca credentials"
```

---

### Task 10: Update server-rendered pages

**Files:**
- Modify: `trading-dashboard/app/page.tsx`
- Modify: `trading-dashboard/app/history/page.tsx`
- Modify: `trading-dashboard/app/pnl/page.tsx`

These are server components. Middleware (Task 6) already redirects unauthenticated requests to `/login` before these render, but each page defensively falls back to demo data if `getAlpacaCreds()` somehow returns null (matching the existing `Promise.allSettled` degrade-to-demo pattern).

- [ ] **Step 1: `app/page.tsx`**

```tsx
export const dynamic = 'force-dynamic'
import { StatsCards }     from '@/components/dashboard/StatsCards'
import { AccountBar }      from '@/components/dashboard/AccountBar'
import { PnLChart }        from '@/components/dashboard/PnLChart'
import { RegimeIndicator } from '@/components/dashboard/RegimeIndicator'
import { SectorHeatmap }   from '@/components/dashboard/SectorHeatmap'
import { PositionsTable }  from '@/components/dashboard/PositionsTable'
import { RefreshButton }   from '@/components/layout/RefreshButton'
import {
  demoStats, demoPnL, demoRegime, demoSectors,
} from '@/lib/api'
import { getAccount, getPositions, type AlpacaCreds } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { botGet } from '@/lib/bot-api'
import type { PortfolioStats, PnLPoint, RegimeInfo, SectorStat } from '@/types/trading'
import type { AlpacaAccount } from '@/lib/alpaca'

async function loadDashboard(creds: AlpacaCreds | null) {
  const [account, positions, stats, pnl, regime, sectors] = await Promise.allSettled([
    creds ? getAccount(creds) : Promise.reject(new Error('no creds')),
    creds ? getPositions(creds) : Promise.reject(new Error('no creds')),
    botGet<PortfolioStats>('/api/stats'),
    botGet<PnLPoint[]>('/api/pnl'),
    botGet<RegimeInfo>('/api/regime'),
    botGet<SectorStat[]>('/api/sectors'),
  ])

  const resolvedStats: PortfolioStats = stats.status === 'fulfilled' ? stats.value : demoStats()
  if (account.status === 'fulfilled') {
    const acc = account.value
    const livePnl   = parseFloat(acc.unrealized_pl) + parseFloat(acc.realized_pl ?? '0')
    const todayPnl  = parseFloat(acc.equity) - parseFloat(acc.last_equity)
    if (!isNaN(livePnl))  resolvedStats.total_pnl = +livePnl.toFixed(2)
    if (!isNaN(todayPnl)) resolvedStats.today_pnl = +todayPnl.toFixed(2)
  }
  if (positions.status === 'fulfilled') {
    resolvedStats.open_positions = positions.value.length
  }

  return {
    stats:     resolvedStats,
    account:   account.status === 'fulfilled' ? account.value : null as AlpacaAccount | null,
    pnl:       pnl.status     === 'fulfilled' ? pnl.value     : demoPnL(),
    regime:    regime.status  === 'fulfilled' ? regime.value  : demoRegime(),
    sectors:   sectors.status === 'fulfilled' ? sectors.value : demoSectors(),
    positions: positions.status === 'fulfilled' ? positions.value : [],
    live:      account.status === 'fulfilled',
  }
}

export default async function DashboardPage() {
  const creds = await getAlpacaCreds()
  const { stats, account, pnl, regime, sectors, positions, live } = await loadDashboard(creds)

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 md:space-y-6 max-w-[1400px]">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg md:text-xl font-bold text-primary">Dashboard</h1>
          <p className="text-xs text-muted mt-0.5 hidden sm:block">
            {new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {live
            ? <span className="flex items-center gap-1.5 text-xs text-bull"><span className="h-1.5 w-1.5 rounded-full bg-bull animate-pulse-slow" />Live</span>
            : <span className="flex items-center gap-1.5 text-xs text-caution"><span className="h-1.5 w-1.5 rounded-full bg-caution" />Demo</span>
          }
          <RefreshButton />
        </div>
      </div>

      <AccountBar account={account} />
      <StatsCards stats={stats} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_220px]">
        <PnLChart data={pnl} />
        <RegimeIndicator regime={regime} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
        <SectorHeatmap sectors={sectors} />
        <PositionsTable positions={positions} />
      </div>
    </div>
  )
}
```

- [ ] **Step 2: `app/history/page.tsx`**

```tsx
export const dynamic = 'force-dynamic'
import { HistoryTable } from '@/components/history/HistoryTable'
import { getOrders, type AlpacaCreds } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { botGet }       from '@/lib/bot-api'
import { demoHistory }  from '@/lib/api'
import type { TradeRecord } from '@/types/trading'

async function loadHistory(creds: AlpacaCreds | null): Promise<{ trades: TradeRecord[]; live: boolean }> {
  const [botHistory, alpacaOrders] = await Promise.allSettled([
    botGet<TradeRecord[]>('/api/history'),
    creds ? getOrders(creds, 'closed', 100) : Promise.reject(new Error('no creds')),
  ])

  const botTrades = botHistory.status === 'fulfilled' ? botHistory.value : []

  // Convert Alpaca filled orders → TradeRecord shape
  const fromAlpaca: TradeRecord[] = alpacaOrders.status === 'fulfilled'
    ? alpacaOrders.value
        .filter(o => parseFloat(o.filled_qty ?? '0') > 0)
        .map(o => ({
          id:        o.id,
          ticker:    o.symbol,
          direction: o.side === 'buy' ? 'LONG' : 'SHORT',
          entry:     parseFloat(o.filled_avg_price ?? '0'),
          exit:      null,
          qty:       parseInt(o.filled_qty),
          pnl:       null,
          pnl_pct:   null,
          opened_at: o.created_at,
          closed_at: o.filled_at,
          duration:  null,
          status:    'closed' as const,
          order_id:  o.id,
        }))
    : []

  // Bot records take precedence (they have P&L calculation)
  const botIds = new Set(botTrades.map(t => t.order_id).filter(Boolean))
  const merged = [
    ...botTrades,
    ...fromAlpaca.filter(t => !botIds.has(t.id)),
  ].sort((a, b) => b.opened_at.localeCompare(a.opened_at))

  const live = botHistory.status === 'fulfilled' || alpacaOrders.status === 'fulfilled'
  return { trades: merged.length ? merged : demoHistory(), live }
}

export default async function HistoryPage() {
  const creds = await getAlpacaCreds()
  const { trades, live } = await loadHistory(creds)

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 md:space-y-6 max-w-[1400px]">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-primary">Trade History</h1>
          <p className="text-xs text-muted mt-0.5">
            {trades.length} trades · sourced from Alpaca + bot records
          </p>
        </div>
        {live
          ? <span className="flex items-center gap-1.5 text-xs text-bull"><span className="h-1.5 w-1.5 rounded-full bg-bull animate-pulse-slow" />Live</span>
          : <span className="text-xs text-caution">Demo data — start bot API for live data</span>
        }
      </div>

      <HistoryTable trades={trades} />
    </div>
  )
}
```

- [ ] **Step 3: `app/pnl/page.tsx`**

```tsx
export const dynamic = 'force-dynamic'
import { PnLAnalytics } from '@/components/pnl/PnLAnalytics'
import { botGet }       from '@/lib/bot-api'
import { getAccount, getOrders, type AlpacaCreds } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'
import { demoPnL, demoStats, demoHistory } from '@/lib/api'
import type { PnLPoint, PortfolioStats, TradeRecord } from '@/types/trading'

async function loadPnL(creds: AlpacaCreds | null) {
  const [pnl, stats, account, orders] = await Promise.allSettled([
    botGet<PnLPoint[]>('/api/pnl'),
    botGet<PortfolioStats>('/api/stats'),
    creds ? getAccount(creds) : Promise.reject(new Error('no creds')),
    creds ? getOrders(creds, 'closed', 200) : Promise.reject(new Error('no creds')),
  ])

  const resolvedStats = stats.status === 'fulfilled' ? stats.value : demoStats()

  // Enrich stats with live Alpaca equity
  if (account.status === 'fulfilled') {
    const acc = account.value
    const livePnl  = parseFloat(acc.unrealized_pl) + parseFloat(acc.realized_pl ?? '0')
    const todayPnl = parseFloat(acc.equity) - parseFloat(acc.last_equity)
    if (!isNaN(livePnl))  resolvedStats.total_pnl = +livePnl.toFixed(2)
    if (!isNaN(todayPnl)) resolvedStats.today_pnl = +todayPnl.toFixed(2)
  }

  const resolvedTrades: TradeRecord[] = orders.status === 'fulfilled'
    ? orders.value.filter(o => parseFloat(o.filled_qty ?? '0') > 0).map(o => ({
        id:        o.id,
        ticker:    o.symbol,
        direction: o.side === 'buy' ? 'LONG' : 'SHORT',
        entry:     parseFloat(o.filled_avg_price ?? '0'),
        exit:      null, qty: parseInt(o.filled_qty),
        pnl: null, pnl_pct: null,
        opened_at: o.created_at, closed_at: o.filled_at,
        duration: null, status: 'closed' as const,
      }))
    : demoHistory()

  const live = pnl.status === 'fulfilled' || account.status === 'fulfilled'

  return {
    pnl:    pnl.status === 'fulfilled' ? pnl.value : demoPnL(),
    stats:  resolvedStats,
    trades: resolvedTrades,
    live,
  }
}

export default async function PnLPage() {
  const creds = await getAlpacaCreds()
  const { pnl, stats, trades, live } = await loadPnL(creds)
  return <PnLAnalytics pnl={pnl} stats={stats} trades={trades} live={live} />
}
```

- [ ] **Step 4: Run `npx tsc --noEmit`**

Expected: no remaining errors about `lib/alpaca.ts` call signatures.

- [ ] **Step 5: Commit**

```bash
git add app/page.tsx app/history/page.tsx app/pnl/page.tsx
git commit -m "feat: dashboard, history, and pnl pages use per-user Alpaca credentials"
```

---

### Task 11: Signup API route

**Files:**
- Create: `trading-dashboard/app/api/auth/signup/route.ts`

- [ ] **Step 1: Write `app/api/auth/signup/route.ts`**

```ts
// trading-dashboard/app/api/auth/signup/route.ts
import { NextResponse } from 'next/server'
import bcrypt from 'bcryptjs'
import { prisma } from '@/lib/prisma'
import { encrypt } from '@/lib/crypto'

interface SignupBody {
  email?:        string
  password?:     string
  alpacaKeyId?:  string
  alpacaSecret?: string
  alpacaPaper?:  boolean
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => null) as SignupBody | null
  if (!body) {
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 })
  }

  const { email, password, alpacaKeyId, alpacaSecret } = body
  if (!email || !password || !alpacaKeyId || !alpacaSecret) {
    return NextResponse.json({ error: 'All fields are required' }, { status: 400 })
  }

  const existing = await prisma.user.findUnique({ where: { email } })
  if (existing) {
    return NextResponse.json({ error: 'An account with this email already exists' }, { status: 409 })
  }

  const paper = body.alpacaPaper !== false
  const base = paper ? 'https://paper-api.alpaca.markets' : 'https://api.alpaca.markets'

  const verify = await fetch(`${base}/v2/account`, {
    headers: {
      'APCA-API-KEY-ID':     alpacaKeyId,
      'APCA-API-SECRET-KEY': alpacaSecret,
    },
  })
  if (!verify.ok) {
    return NextResponse.json({ error: 'Could not verify Alpaca credentials' }, { status: 400 })
  }

  const passwordHash = await bcrypt.hash(password, 10)

  await prisma.user.create({
    data: {
      email,
      passwordHash,
      alpacaKeyId:  encrypt(alpacaKeyId),
      alpacaSecret: encrypt(alpacaSecret),
      alpacaPaper:  paper,
    },
  })

  return NextResponse.json({ success: true })
}
```

- [ ] **Step 2: Commit**

```bash
git add "app/api/auth/signup/route.ts"
git commit -m "feat: add signup endpoint with Alpaca credential verification"
```

---

### Task 12: Login / signup page

**Files:**
- Create: `trading-dashboard/app/login/page.tsx`

- [ ] **Step 1: Write `app/login/page.tsx`**

```tsx
// trading-dashboard/app/login/page.tsx
'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { signIn } from 'next-auth/react'
import { Zap } from 'lucide-react'

export default function LoginPage() {
  const router = useRouter()
  const [mode, setMode]         = useState<'signin' | 'signup'>('signin')
  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm]   = useState('')
  const [alpacaKeyId, setAlpacaKeyId]   = useState('')
  const [alpacaSecret, setAlpacaSecret] = useState('')
  const [alpacaPaper, setAlpacaPaper]   = useState(true)
  const [error, setError]     = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSignIn(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    const res = await signIn('credentials', { email, password, redirect: false })
    setLoading(false)
    if (res?.error) {
      setError('Invalid email or password')
      return
    }
    router.push('/')
    router.refresh()
  }

  async function handleSignUp(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (password !== confirm) {
      setError('Passwords do not match')
      return
    }
    setLoading(true)
    const res = await fetch('/api/auth/signup', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ email, password, alpacaKeyId, alpacaSecret, alpacaPaper }),
    })
    const data = await res.json()
    if (!res.ok) {
      setLoading(false)
      setError(data.error ?? 'Could not create account')
      return
    }
    const signInRes = await signIn('credentials', { email, password, redirect: false })
    setLoading(false)
    if (signInRes?.error) {
      setError('Account created — please sign in')
      setMode('signin')
      return
    }
    router.push('/')
    router.refresh()
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

        <div className="flex rounded-lg bg-bg-base p-1 text-xs font-medium">
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

        <form onSubmit={mode === 'signin' ? handleSignIn : handleSignUp} className="space-y-3">
          <div>
            <label className="text-xs text-muted">Email</label>
            <input
              type="email" required value={email} onChange={e => setEmail(e.target.value)}
              className="mt-1 w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 text-sm text-primary"
            />
          </div>
          <div>
            <label className="text-xs text-muted">Password</label>
            <input
              type="password" required value={password} onChange={e => setPassword(e.target.value)}
              className="mt-1 w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 text-sm text-primary"
            />
          </div>

          {mode === 'signup' && (
            <>
              <div>
                <label className="text-xs text-muted">Confirm password</label>
                <input
                  type="password" required value={confirm} onChange={e => setConfirm(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 text-sm text-primary"
                />
              </div>
              <div>
                <label className="text-xs text-muted">Alpaca API Key ID</label>
                <input
                  type="text" required value={alpacaKeyId} onChange={e => setAlpacaKeyId(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 text-sm text-primary font-mono"
                />
              </div>
              <div>
                <label className="text-xs text-muted">Alpaca Secret Key</label>
                <input
                  type="password" required value={alpacaSecret} onChange={e => setAlpacaSecret(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-bg-border bg-bg-base px-3 py-2 text-sm text-primary font-mono"
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
```

- [ ] **Step 2: Commit**

```bash
git add app/login/page.tsx
git commit -m "feat: add login/signup page"
```

---

### Task 13: Sidebar — user email + log out

**Files:**
- Modify: `trading-dashboard/app/layout.tsx`
- Modify: `trading-dashboard/components/layout/Sidebar.tsx`

- [ ] **Step 1: `app/layout.tsx`**

```tsx
import type { Metadata } from 'next'
import './globals.css'
import { Sidebar, MobileNav } from '@/components/layout/Sidebar'
import { Toaster } from 'sonner'
import { auth } from '@/auth'

export const metadata: Metadata = {
  title: 'Trading Bot Dashboard',
  description: 'AI-powered multi-agent trading intelligence',
  icons: {
    icon: '/favicon.svg',
    shortcut: '/favicon.svg',
  },
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const session = await auth()
  const email = session?.user?.email ?? null

  return (
    <html lang="en" className="dark">
      <body className="flex h-dvh overflow-hidden bg-bg-base text-primary">
        <Sidebar email={email} />
        <main className="flex-1 overflow-y-auto pb-16 md:pb-0">
          {children}
        </main>
        <MobileNav />
        <Toaster
          theme="dark"
          toastOptions={{
            style: {
              background: '#0F172A',
              border: '1px solid #1E293B',
              color: '#F1F5F9',
            },
          }}
        />
      </body>
    </html>
  )
}
```

On `/login`, `auth()` returns `null` (no session), so `email` is `null` — the Sidebar handles that by hiding the user/logout block (Step 2). The Sidebar still renders on `/login` since `app/login/page.tsx` doesn't have its own layout, but it's visually fine (extra nav on the login screen); hiding the Sidebar on `/login` is out of scope for Phase 1.

- [ ] **Step 2: `components/layout/Sidebar.tsx`**

```tsx
'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { signOut } from 'next-auth/react'
import {
  LayoutDashboard, TrendingUp, History, BarChart2,
  Zap, Settings, ExternalLink, FlaskConical, LogOut,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const nav = [
  { href: '/',          icon: LayoutDashboard, label: 'Dashboard'  },
  { href: '/trades',    icon: TrendingUp,      label: 'Trades'     },
  { href: '/history',   icon: History,         label: 'History'    },
  { href: '/pnl',       icon: BarChart2,       label: 'P&L'        },
  { href: '/backtest',  icon: FlaskConical,    label: 'Backtest'   },
]

/** Bottom tab bar shown only on mobile (< md breakpoint) */
export function MobileNav() {
  const path = usePathname()
  return (
    <nav className="md:hidden fixed bottom-0 left-0 right-0 z-50 flex items-center justify-around
                    border-t border-bg-border bg-bg-card/95 backdrop-blur-sm px-2 pb-safe">
      {nav.map(({ href, icon: Icon, label }) => {
        const active = href === '/' ? path === '/' : path.startsWith(href)
        return (
          <Link
            key={href}
            href={href}
            className={cn(
              'flex flex-col items-center gap-0.5 px-2 py-2 rounded-lg text-[10px] font-medium transition-colors min-w-0',
              active ? 'text-brand-cyan' : 'text-muted hover:text-primary',
            )}
          >
            <Icon className="h-5 w-5 shrink-0" />
            <span className="truncate">{label}</span>
          </Link>
        )
      })}
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
  )
}

interface SidebarProps { email: string | null }

export function Sidebar({ email }: SidebarProps) {
  const path = usePathname()
  return (
    <aside className="hidden md:flex w-[220px] flex-col border-r border-bg-border bg-bg-card shrink-0">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 py-5 border-b border-bg-border">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-cyan/10 border border-brand-cyan/30">
          <Zap className="h-4 w-4 text-brand-cyan" />
        </div>
        <div>
          <p className="text-sm font-semibold text-primary leading-tight">TradingBot</p>
          <p className="text-[10px] text-muted leading-tight">AI Intelligence</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        <p className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-widest text-muted/60">
          Navigation
        </p>
        {nav.map(({ href, icon: Icon, label }) => {
          const active = href === '/' ? path === '/' : path.startsWith(href)
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-150',
                active
                  ? 'bg-brand-cyan/10 text-brand-cyan border border-brand-cyan/20'
                  : 'text-subtle hover:bg-bg-hover hover:text-primary'
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
              {active && (
                <div className="ml-auto h-1.5 w-1.5 rounded-full bg-brand-cyan" />
              )}
            </Link>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="border-t border-bg-border px-3 py-3 space-y-0.5">
        {email && (
          <div className="px-3 py-2 text-xs text-muted truncate" title={email}>
            {email}
          </div>
        )}
        <Link
          href="/settings"
          className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-subtle hover:bg-bg-hover hover:text-primary transition-colors"
        >
          <Settings className="h-4 w-4" />
          Settings
        </Link>
        <a
          href="https://github.com/itaitoker64/tradingbot2026"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-subtle hover:bg-bg-hover hover:text-primary transition-colors"
        >
          <ExternalLink className="h-4 w-4" />
          GitHub
        </a>
        {email && (
          <button
            onClick={() => signOut({ callbackUrl: '/login' })}
            className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-subtle hover:bg-bg-hover hover:text-primary transition-colors"
          >
            <LogOut className="h-4 w-4" />
            Log out
          </button>
        )}
      </div>
    </aside>
  )
}
```

- [ ] **Step 3: Run `npx tsc --noEmit`**

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add app/layout.tsx components/layout/Sidebar.tsx
git commit -m "feat: show signed-in user's email and add log out to sidebar"
```

---

### Task 14: Settings page — Alpaca Account card

**Files:**
- Create: `trading-dashboard/app/api/alpaca/settings/route.ts`
- Modify: `trading-dashboard/app/settings/page.tsx`

- [ ] **Step 1: Create `app/api/alpaca/settings/route.ts`**

```ts
// trading-dashboard/app/api/alpaca/settings/route.ts
import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'
import { encrypt } from '@/lib/crypto'

export async function GET() {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const user = await prisma.user.findUnique({ where: { id: session.user.id } })
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  return NextResponse.json({
    alpacaKeyId: user.alpacaKeyId ? `${session.alpaca.keyId.slice(0, 4)}••••${session.alpaca.keyId.slice(-4)}` : '',
    alpacaPaper: user.alpacaPaper,
  })
}

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const body = await req.json().catch(() => null) as {
    alpacaKeyId?: string
    alpacaSecret?: string
    alpacaPaper?: boolean
  } | null
  if (!body?.alpacaKeyId || !body?.alpacaSecret) {
    return NextResponse.json({ error: 'Key ID and secret are required' }, { status: 400 })
  }

  const paper = body.alpacaPaper !== false
  const base = paper ? 'https://paper-api.alpaca.markets' : 'https://api.alpaca.markets'

  const verify = await fetch(`${base}/v2/account`, {
    headers: {
      'APCA-API-KEY-ID':     body.alpacaKeyId,
      'APCA-API-SECRET-KEY': body.alpacaSecret,
    },
  })
  if (!verify.ok) {
    return NextResponse.json({ error: 'Could not verify Alpaca credentials' }, { status: 400 })
  }

  await prisma.user.update({
    where: { id: session.user.id },
    data: {
      alpacaKeyId:  encrypt(body.alpacaKeyId),
      alpacaSecret: encrypt(body.alpacaSecret),
      alpacaPaper:  paper,
    },
  })

  return NextResponse.json({ success: true })
}
```

Note: the new credentials take effect on the user's *next* sign-in (the JWT session carries the credentials decrypted at login time). This is called out in the UI copy in Step 2.

- [ ] **Step 2: Add the "Alpaca Account" card to `app/settings/page.tsx`**

Add a new component above `SettingsPage` (after the existing `ScanStats` interface, before `export default function SettingsPage()`):

```tsx
function AlpacaAccountCard() {
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
      toast.success('Alpaca credentials updated', { description: 'Sign out and back in for changes to take effect' })
      setKeyId('')
      setSecret('')
      setCurrent({ alpacaKeyId: keyId, alpacaPaper: paper })
    } finally {
      setSaving(false)
    }
  }

  return (
    <SettingsCard title="Alpaca Account" icon={Key} iconColor="text-brand-cyan">
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
            <input type="radio" name="settings-paper" checked={paper} onChange={() => setPaper(true)} />
            Paper trading
          </label>
          <label className="flex items-center gap-1.5">
            <input type="radio" name="settings-paper" checked={!paper} onChange={() => setPaper(false)} />
            Live trading
          </label>
        </div>
        <button onClick={save} disabled={saving} className="btn-primary text-xs disabled:opacity-50">
          {saving ? 'Saving…' : 'Save Alpaca credentials'}
        </button>
      </div>
    </SettingsCard>
  )
}
```

Then add `<AlpacaAccountCard />` as the first card inside the `grid grid-cols-1 gap-4 lg:grid-cols-2` block, before the existing `SettingsCard title="Alpaca Paper API"`:

```tsx
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <AlpacaAccountCard />

        {/* Alpaca */}
        <SettingsCard title="Alpaca Paper API" icon={Zap} iconColor="text-brand-cyan">
```

- [ ] **Step 3: Run `npx tsc --noEmit`**

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add "app/api/alpaca/settings/route.ts" app/settings/page.tsx
git commit -m "feat: add Alpaca Account credential management to settings page"
```

---

### Task 15: Wire up `SessionProvider` for `next-auth/react`

**Files:**
- Create: `trading-dashboard/components/providers/SessionProvider.tsx`
- Modify: `trading-dashboard/app/layout.tsx`

`signIn`/`signOut` from `next-auth/react` (used in Tasks 12 and 13) need a `SessionProvider` ancestor in the App Router, even though no component calls `useSession()`.

- [ ] **Step 1: Create the client wrapper**

```tsx
// trading-dashboard/components/providers/SessionProvider.tsx
'use client'
import { SessionProvider as NextAuthSessionProvider } from 'next-auth/react'

export function SessionProvider({ children }: { children: React.ReactNode }) {
  return <NextAuthSessionProvider>{children}</NextAuthSessionProvider>
}
```

- [ ] **Step 2: Wrap the body content in `app/layout.tsx`**

In `trading-dashboard/app/layout.tsx`, import the new provider and wrap the existing `<Sidebar>` / `<main>` / `<MobileNav>` / `<Toaster>` block:

```tsx
import type { Metadata } from 'next'
import './globals.css'
import { Sidebar, MobileNav } from '@/components/layout/Sidebar'
import { SessionProvider } from '@/components/providers/SessionProvider'
import { Toaster } from 'sonner'
import { auth } from '@/auth'

export const metadata: Metadata = {
  title: 'Trading Bot Dashboard',
  description: 'AI-powered multi-agent trading intelligence',
  icons: {
    icon: '/favicon.svg',
    shortcut: '/favicon.svg',
  },
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const session = await auth()
  const email = session?.user?.email ?? null

  return (
    <html lang="en" className="dark">
      <body className="flex h-dvh overflow-hidden bg-bg-base text-primary">
        <SessionProvider>
          <Sidebar email={email} />
          <main className="flex-1 overflow-y-auto pb-16 md:pb-0">
            {children}
          </main>
          <MobileNav />
          <Toaster
            theme="dark"
            toastOptions={{
              style: {
                background: '#0F172A',
                border: '1px solid #1E293B',
                color: '#F1F5F9',
              },
            }}
          />
        </SessionProvider>
      </body>
    </html>
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add components/providers/SessionProvider.tsx app/layout.tsx
git commit -m "feat: wrap app in next-auth SessionProvider for signIn/signOut"
```

---

### Task 16: End-to-end manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite and typecheck**

```bash
cd trading-dashboard
npm test
npx tsc --noEmit
```
Expected: all Vitest tests pass, no TypeScript errors.

- [ ] **Step 2: Set up a real database**

Create a free Neon (or Vercel Postgres) database, put its connection string in `trading-dashboard/.env.local` as `DATABASE_URL`, then run:
```bash
npx prisma migrate dev --name init
```
Expected: `The migration has been applied` and a `User` table exists.

- [ ] **Step 3: Start the dev server**

```bash
npm run dev
```
Expected: server starts on `http://localhost:3000`.

- [ ] **Step 4: Manual signup flow**

Navigate to `http://localhost:3000`. Expected: redirected to `/login` (middleware, Task 6).

On `/login`, switch to "Create account", fill in a test email/password and a real Alpaca **paper** Key ID/Secret (e.g. from `.env.local`'s `ALPACA_KEY_ID`/`ALPACA_SECRET`), leave "Paper trading" selected, submit.

Expected: redirected to `/` (Dashboard), `AccountBar` shows live account data, "Live" badge visible (not "Demo").

- [ ] **Step 5: Manual sign-out / sign-in flow**

Click "Log out" in the sidebar. Expected: redirected to `/login`.

Sign in again with the same email/password (no Alpaca fields this time). Expected: redirected to `/`, same account data shown.

- [ ] **Step 6: Manual execute flow**

On `/trades`, click "Execute" on any recommendation. Expected: success toast; the resulting order appears under `/history` and the new position appears on `/` (`PositionsTable`) — confirming the order landed on the signed-in user's own Alpaca paper account via `submitBracketOrder`.

- [ ] **Step 7: Settings credential update**

On `/settings`, the new "Alpaca Account" card shows the masked current key. Paste the same (or a different) paper Key ID/Secret, save. Expected: success toast. Log out and back in — `AccountBar` still reflects the (possibly new) account.

- [ ] **Step 8: Two-user isolation (optional, requires a second Alpaca paper account)**

Sign up a second user with a different Alpaca paper account's keys. Execute a trade while logged in as each user. Expected: each user's `/history` and `/` positions show only their own order — confirming per-user isolation.

---

## Self-review

**Spec coverage:**
- Database / `User` table → Task 3.
- Credential encryption (`lib/crypto.ts`, `ENCRYPTION_KEY`) → Tasks 2, 4.
- Auth.js v5, JWT session with 30-min sliding expiry, `middleware.ts` → Tasks 4–6.
- `/login` page with sign-in/sign-up modes, Alpaca verification on signup → Tasks 11–12.
- Sidebar email + log out → Task 13.
- `lib/alpaca.ts` refactor + all 12 importers + 2 ad-hoc routes → Tasks 7–10.
- `/api/bot/execute` submits to signed-in user's account, bot notification stays best-effort → Task 9 Step 1.
- Settings page Alpaca credential update → Task 14.
- `.env.example` additions (`DATABASE_URL`, `AUTH_SECRET`, `ENCRYPTION_KEY`) → Tasks 3–4.
- Testing (manual signup/login/idle/two-user, Playwright smoke implied by manual nav checks) → Task 16. (30-min idle timeout itself isn't separately re-verified beyond the configured `maxAge` in Task 5 — manually waiting 30 minutes is impractical within this plan; the `maxAge`/`updateAge` config is the enforcement point.)

**Placeholder scan:** No "TBD"/"TODO"/"similar to Task N" found — every step has complete code or exact commands.

**Type consistency:** `AlpacaCreds { keyId, secret, paper }` defined in Task 7 is used identically in `lib/session.ts` (Task 6), `session.alpaca` (Task 5/`types/next-auth.d.ts` Task 4), and every call site in Tasks 8–10. `getAlpacaCreds()` (Task 6) is the single accessor used by all routes/pages.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-12-multi-user-auth-phase1.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
