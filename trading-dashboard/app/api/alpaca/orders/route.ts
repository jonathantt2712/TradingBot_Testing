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
