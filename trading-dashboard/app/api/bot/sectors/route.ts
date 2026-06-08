import { NextResponse }  from 'next/server'
import { botGet }        from '@/lib/bot-api'
import { demoSectors }   from '@/lib/api'
import type { SectorStat } from '@/types/trading'

export async function GET() {
  try {
    const data = await botGet<SectorStat[]>('/api/sectors')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(demoSectors())
  }
}
