import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { botGet, botPatch } from '@/lib/bot-api'
import { isOwner } from '@/lib/session'

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botGet('/api/optimize/weights')
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Bot offline' }, { status: 502 })
  }
}

export async function PATCH(req: Request) {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!(await isOwner())) {
    return NextResponse.json(
      { error: 'Owner role required — this changes the shared bot for everyone' },
      { status: 403 },
    )
  }

  try {
    const body = await req.json()
    const data = await botPatch('/api/optimize/patch', body)
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Failed to update weights' }, { status: 502 })
  }
}
