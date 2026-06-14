// trading-dashboard/auth.config.ts
//
// Edge-safe NextAuth config shared by auth.ts (Node runtime) and
// middleware.ts (Edge runtime). Keep this file free of Node-only
// dependencies (bcrypt, Prisma, lib/crypto) — anything imported here
// is bundled into the middleware Edge Function.
import type { NextAuthConfig } from 'next-auth'

export const authConfig: NextAuthConfig = {
  session: {
    strategy:  'jwt',
    maxAge:    30 * 60, // 30 minutes
    updateAge: 5 * 60,  // refresh the cookie every 5 minutes of activity
  },
  pages: {
    signIn: '/login',
  },
  providers: [],
  callbacks: {
    async jwt({ token, user, trigger, session }) {
      if (user) {
        token.userId = user.id as string
        token.alpaca = {
          keyId:  (user as unknown as { alpacaKeyId: string }).alpacaKeyId,
          secret: (user as unknown as { alpacaSecret: string }).alpacaSecret,
          paper:  (user as unknown as { alpacaPaper: boolean }).alpacaPaper,
        }
        token.mustChangePassword = (user as unknown as { mustChangePassword: boolean }).mustChangePassword
      }
      if (trigger === 'update' && session?.mustChangePassword === false) {
        token.mustChangePassword = false
      }
      if (trigger === 'update' && session?.alpaca) {
        token.alpaca = session.alpaca
      }
      return token
    },
    async session({ session, token }) {
      session.user.id = token.userId
      session.user.mustChangePassword = token.mustChangePassword
      session.alpaca  = token.alpaca
      return session
    },
  },
}
