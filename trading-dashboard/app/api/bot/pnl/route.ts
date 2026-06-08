import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { demoPnL } from '@/lib/api'

export async function GET() {
  try {
    const data = await botGet('/api/pnl')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(demoPnL())
  }
}
