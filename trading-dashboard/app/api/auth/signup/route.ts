// trading-dashboard/app/api/auth/signup/route.ts
import { NextResponse } from 'next/server'
import bcrypt from 'bcryptjs'
import { prisma } from '@/lib/prisma'
import { encrypt } from '@/lib/crypto'

interface SignupBody {
  email?:        string
  phone?:        string
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

  const { phone, password, alpacaKeyId, alpacaSecret } = body
  const email = body.email?.trim()
  if (!email || !phone || !password || !alpacaKeyId || !alpacaSecret) {
    return NextResponse.json({ error: 'All fields are required' }, { status: 400 })
  }

  const existing = await prisma.user.findFirst({ where: { email: { equals: email, mode: 'insensitive' } } })
  if (existing) {
    return NextResponse.json({ error: 'An account with this email already exists' }, { status: 409 })
  }

  const paper = body.alpacaPaper !== false
  const base = paper ? 'https://paper-api.alpaca.markets' : 'https://api.alpaca.markets'
  const otherBase = paper ? 'https://api.alpaca.markets' : 'https://paper-api.alpaca.markets'

  const alpacaHeaders = {
    'APCA-API-KEY-ID':     alpacaKeyId,
    'APCA-API-SECRET-KEY': alpacaSecret,
  }

  let verify: Response
  try {
    verify = await fetch(`${base}/v2/account`, { headers: alpacaHeaders })
  } catch {
    return NextResponse.json({ error: 'Could not reach Alpaca to verify credentials. Please try again.' }, { status: 502 })
  }

  if (!verify.ok) {
    // Paper and live accounts use separate, non-interchangeable key pairs.
    // If the keys work against the other environment, tell the user to
    // flip the radio button instead of showing a generic error.
    const otherVerify = await fetch(`${otherBase}/v2/account`, { headers: alpacaHeaders }).catch(() => null)
    if (otherVerify?.ok) {
      const hint = paper
        ? 'These look like live trading keys. Select "Live trading" and try again.'
        : 'These look like paper trading keys. Select "Paper trading" and try again.'
      return NextResponse.json({ error: hint }, { status: 400 })
    }
    return NextResponse.json({ error: 'Could not verify Alpaca credentials' }, { status: 400 })
  }

  const passwordHash = await bcrypt.hash(password, 10)

  await prisma.user.create({
    data: {
      email,
      phone,
      passwordHash,
      alpacaKeyId:  encrypt(alpacaKeyId),
      alpacaSecret: encrypt(alpacaSecret),
      alpacaPaper:  paper,
    },
  })

  return NextResponse.json({ success: true })
}
