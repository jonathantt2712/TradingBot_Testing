import { NextResponse } from 'next/server'
import { getAlpacaCreds } from '@/lib/session'

export const dynamic = 'force-dynamic'

/**
 * Returns which Alpaca credentials are configured (without revealing values).
 * Called by the Settings page to show connection status.
 *
 * Credentials can come from two places:
 *   1. The signed-in user's profile (stored encrypted in the DB, surfaced via
 *      the session) — this is how users enter keys in the dashboard.
 *   2. Server-side env vars (.env.local) — used for env-based deployments.
 * We report "set" if EITHER source has them, so a user who entered keys in
 * their profile no longer sees a misleading "missing".
 */
export async function GET() {
  const creds = await getAlpacaCreds()

  const keySet = !!(
    creds?.keyId ||
    process.env.ALPACA_KEY_ID ||
    process.env.ALPACA_API_KEY_ID
  )
  const secretSet = !!(
    creds?.secret ||
    process.env.ALPACA_SECRET ||
    process.env.ALPACA_API_SECRET
  )
  const paper = creds ? creds.paper : process.env.ALPACA_PAPER !== 'false'

  return NextResponse.json({ keySet, secretSet, paper })
}
