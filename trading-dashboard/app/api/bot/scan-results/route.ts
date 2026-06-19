import { NextResponse } from 'next/server'
import { botGet } from '@/lib/bot-api'
import { auth } from '@/auth'

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botGet('/api/scan-results')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ picked: [], rejected: [], scanned_at: null }, { status: 502 })
  }
}
