import { NextResponse } from 'next/server'
import { botGet }       from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const data = await botGet<unknown[]>('/api/exit-decisions')
    return NextResponse.json(Array.isArray(data) ? data : [])
  } catch {
    return NextResponse.json([])
  }
}
