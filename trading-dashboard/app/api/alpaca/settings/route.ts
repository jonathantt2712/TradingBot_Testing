// trading-dashboard/app/api/alpaca/settings/route.ts
import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'
import { encrypt, decrypt } from '@/lib/crypto'

export async function GET() {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const user = await prisma.user.findUnique({ where: { id: session.user.id } })
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const keyId = user.alpacaKeyId ? decrypt(user.alpacaKeyId) : ''

  return NextResponse.json({
    alpacaKeyId: keyId ? `${keyId.slice(0, 4)}••••${keyId.slice(-4)}` : '',
    alpacaPaper: user.alpacaPaper,
  })
}

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const body = await req.json().catch(() => null) as {
    alpacaKeyId?: string
    alpacaSecret?: string
    alpacaPaper?: boolean
  } | null
  if (!body?.alpacaKeyId || !body?.alpacaSecret) {
    return NextResponse.json({ error: 'Key ID and secret are required' }, { status: 400 })
  }

  const paper = body.alpacaPaper !== false
  const base = paper ? 'https://paper-api.alpaca.markets' : 'https://api.alpaca.markets'

  const verify = await fetch(`${base}/v2/account`, {
    headers: {
      'APCA-API-KEY-ID':     body.alpacaKeyId,
      'APCA-API-SECRET-KEY': body.alpacaSecret,
    },
  })
  if (!verify.ok) {
    return NextResponse.json({ error: 'Could not verify Alpaca credentials' }, { status: 400 })
  }

  await prisma.user.update({
    where: { id: session.user.id },
    data: {
      alpacaKeyId:  encrypt(body.alpacaKeyId),
      alpacaSecret: encrypt(body.alpacaSecret),
      alpacaPaper:  paper,
    },
  })

  return NextResponse.json({ success: true })
}
