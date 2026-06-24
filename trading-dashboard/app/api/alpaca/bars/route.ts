import { NextRequest, NextResponse } from 'next/server'
import { getBars } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'

export async function GET(req: NextRequest) {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const { searchParams } = new URL(req.url)
  const rawSymbols = searchParams.get('symbols') ?? ''
  const timeframe  = searchParams.get('timeframe') ?? '5Min'
  const start      = searchParams.get('start') ?? ''
  const limit      = searchParams.get('limit') ?? '78'

  const validatedSymbols = rawSymbols
    .split(',')
    .map(s => s.trim().toUpperCase())
    .filter(s => /^[A-Z0-9.]{1,10}$/.test(s))
    .slice(0, 50)

  if (!validatedSymbols.length) return NextResponse.json({}, { status: 400 })

  const symbols = validatedSymbols.join(',')
  const params = new URLSearchParams({ symbols, timeframe, limit })
  if (start) params.set('start', start)

  try {
    const data = await getBars(creds, params)
    return NextResponse.json(data.bars ?? data)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
