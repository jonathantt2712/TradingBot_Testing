// trading-dashboard/app/api/auth/reset-password/route.ts
import { NextResponse } from 'next/server'
import bcrypt from 'bcryptjs'
import { prisma } from '@/lib/prisma'
import { hashResetToken } from '@/lib/resetToken'

export async function POST(req: Request) {
  const body = await req.json().catch(() => null) as { token?: string; password?: string } | null
  const token = body?.token
  const password = body?.password

  if (!token || !password) {
    return NextResponse.json({ error: 'Token and new password are required' }, { status: 400 })
  }

  const user = await prisma.user.findFirst({
    where: {
      resetTokenHash: hashResetToken(token),
      resetTokenExpiry: { gt: new Date() },
    },
  })

  if (!user) {
    return NextResponse.json({ error: 'This reset link is invalid or has expired' }, { status: 400 })
  }

  const passwordHash = await bcrypt.hash(password, 10)
  await prisma.user.update({
    where: { id: user.id },
    data: { passwordHash, resetTokenHash: null, resetTokenExpiry: null },
  })

  return NextResponse.json({ success: true })
}
