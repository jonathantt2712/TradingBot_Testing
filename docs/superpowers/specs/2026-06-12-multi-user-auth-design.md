# Multi-user Auth + Per-User Alpaca Trading (Phase 1)

## Background

The dashboard (`trading-dashboard/`, Next.js 14 App Router, hosted on Vercel)
currently has no authentication. All Alpaca API calls (`lib/alpaca.ts`) use a
single set of credentials read from environment variables
(`ALPACA_KEY_ID`, `ALPACA_SECRET`, `ALPACA_PAPER`), so the dashboard always
shows and trades one shared account.

The Python bot (`trading_bot/api_server.py`, hosted on Render) is a
continuously-running process that scans the market and trades its own
configured Alpaca account, exposed to the dashboard via `/api/bot/*` proxy
routes (`lib/bot-api.ts`).

## Goal

Add accounts so multiple people can use the same dashboard deployment, each
seeing and trading **their own** Alpaca portfolio:

- A login page gates the whole dashboard. New users sign up by entering
  email/password **and** their Alpaca API Key ID + Secret + Paper/Live
  selection. Returning users sign in with email/password only.
- After sign-in, every per-account view (account summary, positions, order
  history, P&L) and the "Execute" action on a recommendation operate on the
  signed-in user's own Alpaca account.
- Sessions expire after a period of inactivity, returning the user to the
  login page.

## Out of scope (Phase 2, future work)

- The Python bot continues to scan the market and generate recommendations
  for **one** shared account (its own `.env` credentials), unchanged.
  Recommendations shown on `/trades` remain the same for every logged-in
  user — only execution and portfolio views become per-user.
- Giving each user their own independent automated scan/trade loop is a
  separate, much larger project and is not addressed here.

## Architecture

### 1. Database

Add **Postgres** (Vercel Postgres / Neon) + **Prisma** as the ORM for the
Next.js app.

`User` table:

| column         | type      | notes                                  |
|----------------|-----------|----------------------------------------|
| `id`           | uuid (pk) |                                        |
| `email`        | string, unique |                                  |
| `passwordHash` | string    | bcrypt                                |
| `alpacaKeyId`  | string    | AES-256-GCM encrypted at rest          |
| `alpacaSecret` | string    | AES-256-GCM encrypted at rest          |
| `alpacaPaper`  | boolean   | true = paper-api.alpaca.markets        |
| `createdAt`    | datetime  |                                        |

### 2. Credential encryption

New `lib/crypto.ts` module providing `encrypt(text)` / `decrypt(text)` using
AES-256-GCM with a key from a new `ENCRYPTION_KEY` env var (32-byte secret,
base64-encoded, generated once and added to Vercel project env vars for all
environments). Used only for `alpacaKeyId` / `alpacaSecret` at rest in
Postgres.

### 3. Auth (Auth.js / NextAuth v5)

- Add `next-auth@beta` (Auth.js v5, App Router compatible) with a
  **Credentials provider**: email + password, verified against
  `passwordHash` via `bcryptjs`.
- JWT session strategy. The session token (encrypted/signed by Auth.js using
  `AUTH_SECRET`) carries `userId`, `email`, and the **decrypted** Alpaca
  `keyId` / `secret` / `paper` flag, so API routes can read credentials
  directly from the session without a DB round trip per request.
- Session `maxAge` = 30 minutes, sliding (refreshed on activity via Auth.js's
  `updateAge`). An idle tab returns the user to `/login` after ~30 minutes
  of no requests; active use never times out.
- `middleware.ts` protects all routes except `/login` and
  `/api/auth/*`, redirecting unauthenticated requests to `/login`.

### 4. Login / Signup page (`/login`)

Single page, two modes (tab or toggle):

- **Sign in**: email + password fields.
- **Create account**: email, password, confirm password, Alpaca API Key ID,
  Alpaca Secret Key, and a Paper/Live radio toggle.

On signup submission, before creating the `User` row, the server calls
Alpaca's `GET /v2/account` using the supplied keys against the chosen base
URL (`paper-api.alpaca.markets` or `api.alpaca.markets`). If that call fails,
return a validation error and do not create the account. On success, hash
the password, encrypt the Alpaca credentials, create the user, and sign them
in.

The app sidebar (`components/layout/Sidebar.tsx`) gains the signed-in user's
email and a **Log out** button (calls Auth.js `signOut`).

### 5. Per-user Alpaca data wiring

- Refactor `lib/alpaca.ts`: remove the module-level `KEY_ID` / `SECRET` /
  `PAPER` constants and `HEADERS`/`BROKER_BASE` derived from them. Every
  exported function (`getAccount`, `getPositions`, `getOrders`,
  `submitBracketOrder`, etc.) takes an explicit
  `creds: { keyId: string; secret: string; paper: boolean }` argument and
  builds its headers/base URL from that.
- Every route under `app/api/alpaca/*`, plus `app/history/page.tsx` and
  `app/pnl/page.tsx`, call Auth.js's `auth()` to get the session, extract
  `creds`, and pass them into the `lib/alpaca.ts` functions. Unauthenticated
  requests are already blocked by `middleware.ts`, but routes defensively
  return 401 if `auth()` returns no session.
- `app/api/bot/execute/route.ts`: reads `creds` from the session and calls
  `submitBracketOrder(creds, {...})` so the bracket order is placed on the
  **signed-in user's own account** (paper or live per their setting). The
  existing best-effort `botPost('/api/execute', ...)` call to the shared
  Python bot remains, for the bot's own logging, and stays non-blocking
  (`.catch(() => {})`).

### 6. Settings page additions

`app/settings/page.tsx` gains an "Alpaca Account" section where a signed-in
user can update their stored Key ID / Secret / Paper-Live toggle. Same
`/v2/account` validation as signup runs before saving; secrets are
re-encrypted on save. The existing `/api/settings/env` route (which reports
whether *server* env vars are set) stays as-is — it now mainly serves as a
diagnostic for the bot's own `.env`, not the dashboard's per-user Alpaca
keys.

## Data flow summary

```
Browser ──login (email/password)──> Auth.js Credentials provider
                                          │ verifies bcrypt hash
                                          ▼
                                  JWT session cookie
                                  { userId, email, alpaca creds }

Browser ──/trades, /history, /pnl──> Next.js route/page
                                          │ auth() reads session creds
                                          ▼
                                  lib/alpaca.ts (Alpaca REST,
                                  per-user keyId/secret/paper)

Browser ──/api/bot/*────────────────> lib/bot-api.ts ──> shared Python bot
                                  (unchanged, same for all users)
```

## Error handling

- Signup: invalid Alpaca credentials → form error, no user created.
- Login: wrong email/password → generic "invalid credentials" error (no
  user enumeration).
- Session expiry: any page load / API call with an invalid or expired
  session redirects to `/login` (middleware) or returns 401 (API routes,
  which the client treats as "please log in again").
- Alpaca API failures after login (e.g., revoked keys): existing
  `Promise.allSettled` + demo-data fallbacks in `history`/`pnl` pages remain,
  so the dashboard degrades gracefully rather than crashing.

## Testing

- Manual: sign up with real paper-trading Alpaca keys, confirm
  account/positions/orders reflect that account; sign out, sign back in,
  confirm session restored; leave idle >30 min, confirm redirect to
  `/login`.
- Manual: execute a recommendation while logged in as two different test
  users (two different paper accounts), confirm each order lands on the
  correct account.
- Existing Playwright smoke pass on `/`, `/trades`, `/history`, `/pnl` while
  logged in, to confirm no regressions from the `lib/alpaca.ts` signature
  change.
