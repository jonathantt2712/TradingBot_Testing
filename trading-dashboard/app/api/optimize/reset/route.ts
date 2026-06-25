import { NextResponse } from 'next/server'
import { botPost } from '@/lib/bot-api'

export async function POST() {
  try {
    const data = await botPost('/api/optimize/reset', {})
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Failed to reset strategy weights' }, { status: 502 })
  }
}
