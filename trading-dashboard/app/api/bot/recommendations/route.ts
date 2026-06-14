import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'

export async function GET() {
  try {
    const data = await botGet('/api/recommendations')
    return NextResponse.json(data)
  } catch {
    // Bot offline → no data available
    return NextResponse.json([], { status: 502 })
  }
}
