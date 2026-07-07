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
    maxAge:    8 * 60 * 60, // 8 hours
    updateAge: 5 * 60,      // refresh the cookie every 5 minutes of activity
  },
  pages: {
    signIn: '/login',
  },
  providers: [],
  callbacks: {
    // NOTE: broker credentials deliberately do NOT ride in the JWT. They used
    // to — which put the (decrypted) Alpaca secret inside a browser cookie on
    // every request, decryptable by anyone holding AUTH_SECRET. Server code
    // fetches and decrypts them from the DB per request (lib/session.ts).
    async jwt({ token, user, trigger, session }) {
      if (user) {
        token.userId = user.id as string
        token.mustChangePassword = (user as unknown as { mustChangePassword: boolean }).mustChangePassword
      }
      if (trigger === 'update' && session?.mustChangePassword === false) {
        token.mustChangePassword = false
      }
      return token
    },
    async session({ session, token }) {
      session.user.id = token.userId
      session.user.mustChangePassword = token.mustChangePassword
      return session
    },
  },
}
