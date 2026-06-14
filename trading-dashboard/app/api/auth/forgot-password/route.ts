// trading-dashboard/app/api/auth/forgot-password/route.ts
import { NextResponse } from 'next/server'
import bcrypt from 'bcryptjs'
import { prisma } from '@/lib/prisma'
import { generateTemporaryPassword } from '@/lib/generatePassword'
import { sendTemporaryPasswordEmail } from '@/lib/brevo'

export async function POST(req: Request) {
  const body = await req.json().catch(() => null) as { email?: string } | null
  const email = body?.email?.trim()

  if (email) {
    const user = await prisma.user.findFirst({ where: { email: { equals: email, mode: 'insensitive' } } })
    if (user) {
      const tempPassword = generateTemporaryPassword()
      const passwordHash = await bcrypt.hash(tempPassword, 10)
      await prisma.user.update({
        where: { id: user.id },
        data: { passwordHash, mustChangePassword: true },
      })

      try {
        await sendTemporaryPasswordEmail(email, tempPassword)
      } catch (err) {
        console.error('Failed to send password reset email', err)
      }
    }
  }

  // Always return success — don't reveal whether the email is registered.
  return NextResponse.json({ success: true })
}
