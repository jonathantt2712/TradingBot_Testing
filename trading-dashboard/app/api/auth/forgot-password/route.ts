// trading-dashboard/app/api/auth/forgot-password/route.ts
import { NextResponse } from 'next/server'
import { prisma } from '@/lib/prisma'
import { generateResetToken } from '@/lib/resetToken'
import { sendPasswordResetEmail } from '@/lib/brevo'

export async function POST(req: Request) {
  const body = await req.json().catch(() => null) as { email?: string } | null
  const email = body?.email

  if (email) {
    const user = await prisma.user.findUnique({ where: { email } })
    if (user) {
      const { token, hash, expiresAt } = generateResetToken()
      await prisma.user.update({
        where: { id: user.id },
        data: { resetTokenHash: hash, resetTokenExpiry: expiresAt },
      })

      const host = req.headers.get('x-forwarded-host') ?? req.headers.get('host')
      const protocol = req.headers.get('x-forwarded-proto') ?? new URL(req.url).protocol.replace(':', '')
      const origin = host ? `${protocol}://${host}` : new URL(req.url).origin
      const resetUrl = `${origin}/reset-password?token=${token}`

      try {
        await sendPasswordResetEmail(email, resetUrl)
      } catch (err) {
        console.error('Failed to send password reset email', err)
      }
    }
  }

  // Always return success — don't reveal whether the email is registered.
  return NextResponse.json({ success: true })
}
