// trading-dashboard/auth.ts
import NextAuth from 'next-auth'
import Credentials from 'next-auth/providers/credentials'
import bcrypt from 'bcryptjs'
import { prisma } from '@/lib/prisma'
import { decrypt } from '@/lib/crypto'
import { authConfig } from './auth.config'

export const { handlers, auth, signIn, signOut } = NextAuth({
  ...authConfig,
  providers: [
    Credentials({
      credentials: {
        email:    { label: 'Email',    type: 'email' },
        password: { label: 'Password', type: 'password' },
      },
      async authorize(credentials) {
        const email    = (credentials?.email as string | undefined)?.trim()
        const password = credentials?.password as string | undefined
        if (!email || !password) return null

        const user = await prisma.user.findFirst({ where: { email: { equals: email, mode: 'insensitive' } } })

        // Always run bcrypt.compare, even if the user doesn't exist, so the
        // response time doesn't leak whether the email is registered.
        const DUMMY_HASH = '$2a$10$CwTycUXWue0Thq9StjUM0uJ8B9d.bHbR0bM4F.RnXi7B22.HEZk7C'
        const valid = await bcrypt.compare(password, user?.passwordHash ?? DUMMY_HASH)
        if (!user || !valid) return null

        return {
          id:                 user.id,
          email:              user.email,
          alpacaKeyId:        decrypt(user.alpacaKeyId),
          alpacaSecret:       decrypt(user.alpacaSecret),
          alpacaPaper:        user.alpacaPaper,
          mustChangePassword: user.mustChangePassword,
        }
      },
    }),
  ],
})
