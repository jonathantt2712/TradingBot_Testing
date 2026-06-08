import { NextResponse } from 'next/server'

/**
 * Returns which env vars are configured (without revealing values).
 * Called by the Settings page to show connection status.
 */
export async function GET() {
  return NextResponse.json({
    keySet:    !!(process.env.ALPACA_KEY_ID     && process.env.ALPACA_KEY_ID     !== ''),
    secretSet: !!(process.env.ALPACA_SECRET     && process.env.ALPACA_SECRET     !== ''),
    paper:     process.env.ALPACA_PAPER !== 'false',
  })
}
