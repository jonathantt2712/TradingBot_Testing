import type { DefaultSession } from 'next-auth'

declare module 'next-auth' {
  interface Session {
    user: {
      id: string
    } & DefaultSession['user']
    alpaca: {
      keyId:  string
      secret: string
      paper:  boolean
    }
  }

  interface User {
    alpacaKeyId:  string
    alpacaSecret: string
    alpacaPaper:  boolean
  }
}

declare module '@auth/core/jwt' {
  interface JWT {
    userId: string
    alpaca: {
      keyId:  string
      secret: string
      paper:  boolean
    }
  }
}
