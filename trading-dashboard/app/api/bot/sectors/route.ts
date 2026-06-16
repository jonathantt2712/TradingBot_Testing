import { NextResponse }  from 'next/server'
import { botGet }        from '@/lib/bot-api'
import { demoSectors }   from '@/lib/api'
import type { SectorStat } from '@/types/trading'
import { auth } from '@/auth'

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botGet<SectorStat[]>('/api/sectors')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(demoSectors())
  }
}
