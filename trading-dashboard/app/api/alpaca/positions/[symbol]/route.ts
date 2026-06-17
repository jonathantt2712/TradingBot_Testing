import { NextRequest, NextResponse } from 'next/server'
import { closePosition } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'

export async function DELETE(
  _req: NextRequest,
  { params }: { params: { symbol: string } },
) {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ message: 'Unauthorized' }, { status: 401 })

  const symbol = decodeURIComponent(params.symbol).toUpperCase()

  try {
    const order = await closePosition(creds, symbol)
    return NextResponse.json({ ok: true, symbol, order })
  } catch (err: any) {
    console.error(`[close-position] ${symbol}:`, err.message)
    return NextResponse.json({ message: err.message ?? 'Unknown error' }, { status: 502 })
  }
}
