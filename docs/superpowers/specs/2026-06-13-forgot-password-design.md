# Forgot Password Design

## Goal

Let a user who forgot their password request a reset link by email, follow
it to a reset-password page, set a new password, and sign in with it.

## Architecture

Standard token-based reset flow:

1. User clicks "Forgot password?" on `/login`, lands on `/forgot-password`.
2. User submits their email. The API always responds with a generic success
   message (does not reveal whether the email is registered).
3. If the email matches a user, the API generates a random token, stores its
   SHA-256 hash and a 1-hour expiry on the `User` row, and emails a reset
   link via Brevo.
4. User opens the link (`/reset-password?token=<rawToken>`), enters a new
   password (with confirm + show/hide toggle, matching `/login`'s pattern).
5. The API hashes the submitted token, looks up a user whose
   `resetTokenHash` matches and `resetTokenExpiry` is in the future, updates
   `passwordHash`, and clears the token fields.
6. User is redirected to `/login` with a success message and signs in with
   the new password.

## Data Model

Add two nullable fields to `User` in `trading-dashboard/prisma/schema.prisma`:

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

A new request overwrites any previous token (only one active reset link per
user). Successful reset clears both fields back to `null`.

## Email Sending

New file `trading-dashboard/lib/brevo.ts` exports `sendPasswordResetEmail(to: string, resetUrl: string)`,
which POSTs to `https://api.brevo.com/v3/smtp/email` with header
`api-key: process.env.BREVO_API_KEY`. Sender is
`{ email: process.env.BREVO_SENDER_EMAIL, name: 'TradingBot' }`. Plain
implementation via `fetch` — no new npm dependency.

Env vars (already added to `.env.local`, not committed):
- `BREVO_API_KEY`
- `BREVO_SENDER_EMAIL` = `ttradingbott@gmail.com`

The reset link's base URL is derived from the incoming request's
`host` / `x-forwarded-proto` headers (works in dev and on Vercel without
extra config).

## Routes

### `POST /api/auth/forgot-password`

- Body: `{ email: string }`
- Looks up user by email. If found: generate `crypto.randomBytes(32)` token,
  store `sha256(token)` as `resetTokenHash`, set `resetTokenExpiry = now + 1h`,
  send email with link `${origin}/reset-password?token=${rawToken}`.
- Always returns `{ success: true }` (200), regardless of whether the user
  exists, to avoid leaking account existence.
- If Brevo's API call fails, log the error server-side but still return the
  generic success response (don't leak delivery failures to the client for
  the same reason).

### `POST /api/auth/reset-password`

- Body: `{ token: string, password: string }`
- Validates `password` is present (reuse existing signup validation style —
  no extra strength rules beyond what signup already enforces, i.e. none).
- Hashes `token` with SHA-256, looks up user where `resetTokenHash` matches
  AND `resetTokenExpiry > now`.
- No match → `{ error: 'This reset link is invalid or has expired' }`, 400.
- Match → bcrypt-hash the new password into `passwordHash`, set
  `resetTokenHash = null`, `resetTokenExpiry = null`.
- Returns `{ success: true }`.

## Pages

### `/forgot-password` (new, `app/forgot-password/page.tsx`)

Client component matching `/login`'s visual style (same card, logo,
`btn-primary`). Single email input + submit button. On submit, POST to
`/api/auth/forgot-password`, then show the generic success message
("If an account exists for that email, we've sent a reset link") regardless
of response — replaces the form with the message. Link back to `/login`.

### `/reset-password` (new, `app/reset-password/page.tsx`)

Client component, same visual style. Reads `token` from the URL query
string (`useSearchParams`). Two fields: "New password" and "Confirm
password", with the same show/hide toggle pattern as `/login`. Client-side
check that the two match (same as signup). On submit, POST to
`/api/auth/reset-password` with `{ token, password }`.
- On success: show a brief success message and redirect to `/login` after a
  short delay (or immediately, with a "Password updated — sign in below"
  message carried via a query param, e.g. `/login?reset=success`).
- On error: show the returned error message (e.g. expired/invalid link)
  inline, same as `/login`'s error display.
- If `token` is missing from the URL entirely, show an inline error and a
  link back to `/forgot-password` (no API call).

### `/login` changes

In sign-in mode, add a "Forgot password?" link below the password field,
pointing to `/forgot-password`. If `?reset=success` is present in the query
string, show a one-time success message (e.g. "Password updated — sign in
with your new password").

## Middleware

`trading-dashboard/middleware.ts` currently allows only `/login` without a
session. Add `/forgot-password` and `/reset-password` to the set of
unauthenticated-allowed page paths, e.g.:

```ts
const PUBLIC_PATHS = ['/login', '/forgot-password', '/reset-password']
...
const isPublicPage = PUBLIC_PATHS.includes(req.nextUrl.pathname)
if (!isLoggedIn && !isPublicPage) { ... }
if (isLoggedIn && isPublicPage) { ... }
```

`app/layout.tsx` already hides the sidebar/chrome whenever there's no
session, so the new pages get the chrome-less layout for free.

## Error Handling

- Forgot-password endpoint never reveals whether an email is registered.
- Reset-password endpoint distinguishes only "invalid/expired token" vs
  success — doesn't leak which.
- Network/API errors when calling Brevo are caught and logged
  server-side; the user-facing response stays generic (per above).
- Both new pages follow `/login`'s existing `try/catch` +
  "Something went wrong — please try again" pattern for unexpected
  fetch failures.

## Testing

- `trading-dashboard/tests` (Vitest) — add unit tests for the two new API
  routes:
  - `forgot-password`: returns success for both existing and non-existing
    emails; sets `resetTokenHash`/`resetTokenExpiry` only for existing
    users; mocks the Brevo fetch call.
  - `reset-password`: rejects missing/invalid/expired tokens; succeeds with
    a valid token, updates `passwordHash`, clears token fields; rejects a
    reused token (fields already cleared → no match).
- `npx tsc --noEmit` and `npm test` as the verification gates, per existing
  convention.

## Out of Scope

- Password strength requirements (matches existing signup, which has none).
- Rate limiting on forgot-password requests (acceptable for current scale;
  can be added later if abused).
- Brevo domain/DKIM verification beyond the sender address already verified.
