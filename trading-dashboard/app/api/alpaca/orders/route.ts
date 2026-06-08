import { NextResponse } from 'next/server'
import { getOrders } from '@/lib/alpaca'

export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url)
    const status = searchParams.get('status') ?? 'closed'
    const limit  = parseInt(searchParams.get('limit') ?? '50')
    const orders = await getOrders(status, limit)
    return NextResponse.json(orders)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
