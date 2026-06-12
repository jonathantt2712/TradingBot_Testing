import { NextResponse } from 'next/server'
import { getPositions } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'

export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const positions = await getPositions(creds)
    return NextResponse.json(positions)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
