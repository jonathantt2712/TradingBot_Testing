import { NextRequest, NextResponse } from 'next/server'

const KEY_ID   = process.env.ALPACA_KEY_ID ?? ''
const SECRET   = process.env.ALPACA_SECRET  ?? ''
const BASE_URL = 'https://paper-api.alpaca.markets'

export async function DELETE(
  _req: NextRequest,
  { params }: { params: { symbol: string } },
) {
  const symbol = decodeURIComponent(params.symbol).toUpperCase()

  try {
    const res = await fetch(`${BASE_URL}/v2/positions/${symbol}`, {
      method:  'DELETE',
      headers: {
        'APCA-API-KEY-ID':     KEY_ID,
        'APCA-API-SECRET-KEY': SECRET,
      },
    })

    // 204 = position closed successfully (no body)
    if (res.status === 204) {
      return NextResponse.json({ ok: true, symbol })
    }

    const data = await res.json().catch(() => ({}))

    if (!res.ok) {
      return NextResponse.json(
        { message: data.message ?? `Alpaca ${res.status}` },
        { status: res.status },
      )
    }

    // 200 = returns an order object
    return NextResponse.json({ ok: true, symbol, order: data })
  } catch (err: any) {
    return NextResponse.json({ message: err.message }, { status: 502 })
  }
}
