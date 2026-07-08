import { NextResponse } from 'next/server'
import { getOrders } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'

/** Debug endpoint — returns raw Alpaca orders to diagnose history P&L issues. */
export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const orders = await getOrders(creds, 'closed', 50).catch((e: Error) => ({ error: e.message }))
  return NextResponse.json(orders)
}
