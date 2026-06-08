import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { demoRecommendations } from '@/lib/api'

export async function GET() {
  try {
    const data = await botGet('/api/recommendations')
    return NextResponse.json(data)
  } catch {
    // Bot offline → return demo data so UI never breaks
    return NextResponse.json(demoRecommendations())
  }
}
