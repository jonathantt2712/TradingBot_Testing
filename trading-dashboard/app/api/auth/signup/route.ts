// trading-dashboard/app/api/auth/signup/route.ts
import { NextResponse } from 'next/server'
import bcrypt from 'bcryptjs'
import { prisma } from '@/lib/prisma'
import { encrypt } from '@/lib/crypto'

interface SignupBody {
  email?:        string
  password?:     string
  alpacaKeyId?:  string
  alpacaSecret?: string
  alpacaPaper?:  boolean
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => null) as SignupBody | null
  if (!body) {
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 })
  }

  const { email, password, alpacaKeyId, alpacaSecret } = body
  if (!email || !password || !alpacaKeyId || !alpacaSecret) {
    return NextResponse.json({ error: 'All fields are required' }, { status: 400 })
  }

  const existing = await prisma.user.findUnique({ where: { email } })
  if (existing) {
    return NextResponse.json({ error: 'An account with this email already exists' }, { status: 409 })
  }

  const paper = body.alpacaPaper !== false
  const base = paper ? 'https://paper-api.alpaca.markets' : 'https://api.alpaca.markets'

  const verify = await fetch(`${base}/v2/account`, {
    headers: {
      'APCA-API-KEY-ID':     alpacaKeyId,
      'APCA-API-SECRET-KEY': alpacaSecret,
    },
  })
  if (!verify.ok) {
    return NextResponse.json({ error: 'Could not verify Alpaca credentials' }, { status: 400 })
  }

  const passwordHash = await bcrypt.hash(password, 10)

  await prisma.user.create({
    data: {
      email,
      passwordHash,
      alpacaKeyId:  encrypt(alpacaKeyId),
      alpacaSecret: encrypt(alpacaSecret),
      alpacaPaper:  paper,
    },
  })

  return NextResponse.json({ success: true })
}
