import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'

const PHONE_REGEX = /^\+?\d{6,15}$/

export async function PATCH(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const body = await req.json().catch(() => null) as { phone?: string } | null
  const phone = body?.phone

  if (phone === undefined) {
    return NextResponse.json({ error: 'Phone is required' }, { status: 400 })
  }

  if (phone !== '' && !PHONE_REGEX.test(phone)) {
    return NextResponse.json({ error: 'Enter a valid phone number' }, { status: 400 })
  }

  const updated = await prisma.user.update({
    where: { id: session.user.id },
    data: { phone: phone === '' ? null : phone },
  })

  return NextResponse.json({ success: true, phone: updated.phone })
}
