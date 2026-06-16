import { NextResponse } from 'next/server'
import { botGet }       from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const data = await botGet<Record<string, unknown>>('/api/regime-performance')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({})
  }
}
