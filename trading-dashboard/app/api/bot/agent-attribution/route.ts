import { NextResponse } from 'next/server'
import { botGet }       from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const data = await botGet<Record<string, { wins: number; losses: number; total: number; win_rate: number; total_pnl: number }>>('/api/agent-attribution')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({})
  }
}
