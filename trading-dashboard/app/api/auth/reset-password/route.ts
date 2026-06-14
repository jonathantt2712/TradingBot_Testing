// trading-dashboard/app/api/auth/reset-password/route.ts
import { NextResponse } from 'next/server'
import bcrypt from 'bcryptjs'
import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const body = await req.json().catch(() => null) as { password?: string } | null
  const password = body?.password

  if (!password) {
    return NextResponse.json({ error: 'A new password is required' }, { status: 400 })
  }

  const passwordHash = await bcrypt.hash(password, 10)
  await prisma.user.update({
    where: { id: session.user.id },
    data: { passwordHash, mustChangePassword: false },
  })

  return NextResponse.json({ success: true })
}
