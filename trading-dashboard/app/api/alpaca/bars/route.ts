import { NextRequest, NextResponse } from 'next/server'

const KEY_ID    = process.env.ALPACA_KEY_ID ?? ''
const SECRET    = process.env.ALPACA_SECRET  ?? ''
const DATA_BASE = 'https://data.alpaca.markets'

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url)
  const symbols   = searchParams.get('symbols') ?? ''
  const timeframe = searchParams.get('timeframe') ?? '5Min'
  const start     = searchParams.get('start') ?? ''
  const limit     = searchParams.get('limit') ?? '78'

  if (!symbols) return NextResponse.json({}, { status: 400 })

  const params = new URLSearchParams({ symbols, timeframe, limit })
  if (start) params.set('start', start)

  try {
    const res = await fetch(`${DATA_BASE}/v2/stocks/bars?${params}`, {
      headers: {
        'APCA-API-KEY-ID':     KEY_ID,
        'APCA-API-SECRET-KEY': SECRET,
      },
      cache: 'no-store',
    })
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText)
      return NextResponse.json({ error: text }, { status: res.status })
    }
    const data = await res.json()
    // Alpaca returns { bars: { AAPL: [...], ... } }
    return NextResponse.json(data.bars ?? data)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
