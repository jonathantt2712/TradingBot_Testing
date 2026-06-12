// trading-dashboard/lib/session.ts
import { auth } from '@/auth'
import type { AlpacaCreds } from '@/lib/alpaca'

/** Returns the signed-in user's Alpaca credentials, or null if unauthenticated. */
export async function getAlpacaCreds(): Promise<AlpacaCreds | null> {
  const session = await auth()
  if (!session?.alpaca) return null
  return session.alpaca
}
