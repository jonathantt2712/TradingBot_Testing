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
