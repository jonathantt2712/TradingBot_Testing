import type { DefaultSession } from 'next-auth'

declare module 'next-auth' {
  interface Session {
    user: {
      id: string
      mustChangePassword: boolean
    } & DefaultSession['user']
    alpaca: {
      keyId:  string
      secret: string
      paper:  boolean
    }
  }

  interface User {
    alpacaKeyId:        string
    alpacaSecret:       string
    alpacaPaper:        boolean
    mustChangePassword: boolean
  }
}

// next-auth/jwt is just a re-export of @auth/core/jwt — augment the real module so the JWT type merge applies
declare module '@auth/core/jwt' {
  interface JWT {
    userId: string
    mustChangePassword: boolean
    alpaca: {
      keyId:  string
      secret: string
      paper:  boolean
    }
  }
}
