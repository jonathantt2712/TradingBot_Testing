// trading-dashboard/app/api/auth/signup/route.ts
import { timingSafeEqual } from 'crypto'
import { NextResponse } from 'next/server'
import bcrypt from 'bcryptjs'
import { prisma } from '@/lib/prisma'
import { encrypt } from '@/lib/crypto'

interface SignupBody {
  email?:        string
  phone?:        string
  password?:     string
  inviteCode?:   string
  alpacaKeyId?:  string
  alpacaSecret?: string
  alpacaPaper?:  boolean
}

function inviteCodeValid(code: string | undefined): boolean {
  const expected = process.env.SIGNUP_INVITE_CODE ?? ''
  if (!expected || !code) return false
  const a = Buffer.from(code)
  const b = Buffer.from(expected)
  return a.length === b.length && timingSafeEqual(a, b)
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => null) as SignupBody | null
  if (!body) {
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 })
  }

  // Registration is invite-only: this dashboard controls a SHARED bot, so an
  // open signup would hand shared controls (auto-execute toggle, broker
  // switch, optimizer) to anyone with free Alpaca paper keys. Fail closed
  // when no invite code is configured.
  if (!process.env.SIGNUP_INVITE_CODE) {
    return NextResponse.json(
      { error: 'Sign-ups are disabled — SIGNUP_INVITE_CODE is not configured' },
      { status: 403 },
    )
  }
  if (!inviteCodeValid(body.inviteCode)) {
    return NextResponse.json({ error: 'Invalid invite code' }, { status: 403 })
  }

  const { phone, password, alpacaKeyId, alpacaSecret } = body
  const email = body.email?.trim()
  if (!email || !password || !alpacaKeyId || !alpacaSecret) {
    return NextResponse.json({ error: 'All fields are required' }, { status: 400 })
  }

  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return NextResponse.json({ error: 'Invalid email address' }, { status: 400 })
  }
  if (password.length < 8) {
    return NextResponse.json({ error: 'Password must be at least 8 characters' }, { status: 400 })
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

  // First account on a fresh install is the operator; everyone invited later
  // is a viewer (owner can promote via the database if needed).
  const userCount = await prisma.user.count()

  await prisma.user.create({
    data: {
      email,
      phone: phone || null,
      passwordHash,
      alpacaKeyId:  encrypt(alpacaKeyId),
      alpacaSecret: encrypt(alpacaSecret),
      alpacaPaper:  paper,
      role:         userCount === 0 ? 'owner' : 'viewer',
    },
  })

  return NextResponse.json({ success: true })
}
