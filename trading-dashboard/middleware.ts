// trading-dashboard/middleware.ts
import { NextResponse } from 'next/server'
import { auth } from '@/auth'

export default auth((req) => {
  const isLoggedIn = !!req.auth
  const isLoginPage = req.nextUrl.pathname === '/login'

  if (!isLoggedIn && !isLoginPage) {
    return NextResponse.redirect(new URL('/login', req.nextUrl))
  }
  if (isLoggedIn && isLoginPage) {
    return NextResponse.redirect(new URL('/', req.nextUrl))
  }
})

export const config = {
  // Protect page routes only; /api/* routes check auth() themselves and return 401.
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico|favicon.svg).*)'],
}
