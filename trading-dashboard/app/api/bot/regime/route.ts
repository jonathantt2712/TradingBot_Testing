import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { demoRegime } from '@/lib/api'

export async function GET() {
  try {
    const data = await botGet('/api/regime')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(demoRegime())
  }
}
