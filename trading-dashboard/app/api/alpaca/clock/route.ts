import { NextResponse } from 'next/server'
import { getClock } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'

export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const clock = await getClock(creds)
    return NextResponse.json(clock)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
