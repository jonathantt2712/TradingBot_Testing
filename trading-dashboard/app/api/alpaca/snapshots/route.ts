import { NextResponse } from 'next/server'
import { getSnapshots } from '@/lib/alpaca'

export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url)
    const symbols = (searchParams.get('symbols') ?? 'SPY,QQQ,AAPL,NVDA,MSFT')
      .split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
    const snaps = await getSnapshots(symbols)
    return NextResponse.json(snaps)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
