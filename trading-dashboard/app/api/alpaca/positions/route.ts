import { NextResponse } from 'next/server'
import { getPositions } from '@/lib/alpaca'

export async function GET() {
  try {
    const positions = await getPositions()
    return NextResponse.json(positions)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
