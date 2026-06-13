// trading-dashboard/middleware.ts
import { NextResponse } from 'next/server'
import { auth } from '@/auth'

const PUBLIC_PATHS = ['/login', '/forgot-password', '/reset-password']

export default auth((req) => {
  const isLoggedIn = !!req.auth
  const isPublicPage = PUBLIC_PATHS.includes(req.nextUrl.pathname)

  if (!isLoggedIn && !isPublicPage) {
    return NextResponse.redirect(new URL('/login', req.nextUrl))
  }
  if (isLoggedIn && isPublicPage) {
    return NextResponse.redirect(new URL('/', req.nextUrl))
  }
})

export const config = {
  // Protect page routes only; /api/* routes check auth() themselves and return 401.
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico|favicon.svg).*)'],
}
