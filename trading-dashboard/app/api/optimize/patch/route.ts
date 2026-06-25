import { NextResponse } from 'next/server'
import { botGet, botPatch } from '@/lib/bot-api'

export async function GET() {
  try {
    const data = await botGet('/api/optimize/weights')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Bot offline' }, { status: 502 })
  }
}

export async function PATCH(req: Request) {
  try {
    const body = await req.json()
    const data = await botPatch('/api/optimize/patch', body)
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Failed to update weights' }, { status: 502 })
  }
}
