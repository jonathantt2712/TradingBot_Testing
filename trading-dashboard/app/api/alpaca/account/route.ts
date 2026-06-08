import { NextResponse } from 'next/server'
import { getAccount } from '@/lib/alpaca'

export async function GET() {
  try {
    const account = await getAccount()
    return NextResponse.json(account)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
