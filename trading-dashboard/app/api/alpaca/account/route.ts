import { NextResponse } from 'next/server'
import { getAccount } from '@/lib/alpaca'
import { getAlpacaCreds } from '@/lib/session'

export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  try {
    const account = await getAccount(creds)
    return NextResponse.json(account)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
