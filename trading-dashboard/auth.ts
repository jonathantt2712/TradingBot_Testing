// trading-dashboard/auth.ts
import NextAuth from 'next-auth'
import Credentials from 'next-auth/providers/credentials'
import bcrypt from 'bcryptjs'
import { prisma } from '@/lib/prisma'
import { decrypt } from '@/lib/crypto'

export const { handlers, auth, signIn, signOut } = NextAuth({
  session: {
    strategy:  'jwt',
    maxAge:    30 * 60, // 30 minutes
    updateAge: 5 * 60,  // refresh the cookie every 5 minutes of activity
  },
  pages: {
    signIn: '/login',
  },
  providers: [
    Credentials({
      credentials: {
        email:    { label: 'Email',    type: 'email' },
        password: { label: 'Password', type: 'password' },
      },
      async authorize(credentials) {
        const email    = credentials?.email as string | undefined
        const password = credentials?.password as string | undefined
        if (!email || !password) return null

        const user = await prisma.user.findUnique({ where: { email } })

        // Always run bcrypt.compare, even if the user doesn't exist, so the
        // response time doesn't leak whether the email is registered.
        const DUMMY_HASH = '$2a$10$CwTycUXWue0Thq9StjUM0uJ8B9d.bHbR0bM4F.RnXi7B22.HEZk7C'
        const valid = await bcrypt.compare(password, user?.passwordHash ?? DUMMY_HASH)
        if (!user || !valid) return null

        return {
          id:           user.id,
          email:        user.email,
          alpacaKeyId:  decrypt(user.alpacaKeyId),
          alpacaSecret: decrypt(user.alpacaSecret),
          alpacaPaper:  user.alpacaPaper,
        }
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.userId = user.id as string
        token.alpaca = {
          keyId:  (user as unknown as { alpacaKeyId: string }).alpacaKeyId,
          secret: (user as unknown as { alpacaSecret: string }).alpacaSecret,
          paper:  (user as unknown as { alpacaPaper: boolean }).alpacaPaper,
        }
      }
      return token
    },
    async session({ session, token }) {
      session.user.id = token.userId
      session.alpaca  = token.alpaca
      return session
    },
  },
})
