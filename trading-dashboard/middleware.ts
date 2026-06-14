// trading-dashboard/middleware.ts
import { NextResponse } from 'next/server'
import { auth } from '@/auth'

const PUBLIC_PATHS = ['/login', '/forgot-password']
const RESET_PASSWORD_PATH = '/reset-password'

export default auth((req) => {
  const isLoggedIn = !!req.auth
  const pathname = req.nextUrl.pathname
  const isPublicPage = PUBLIC_PATHS.includes(pathname)

  if (!isLoggedIn) {
    if (pathname === RESET_PASSWORD_PATH || (!isPublicPage)) {
      return NextResponse.redirect(new URL('/login', req.nextUrl))
    }
    return
  }

  const mustChangePassword = req.auth?.user?.mustChangePassword
  if (mustChangePassword && pathname !== RESET_PASSWORD_PATH) {
    return NextResponse.redirect(new URL(RESET_PASSWORD_PATH, req.nextUrl))
  }
  if (!mustChangePassword && pathname === RESET_PASSWORD_PATH) {
    return NextResponse.redirect(new URL('/', req.nextUrl))
  }
  if (isPublicPage) {
    return NextResponse.redirect(new URL('/', req.nextUrl))
  }
})

export const config = {
  // Protect page routes only; /api/* routes check auth() themselves and return 401.
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico|favicon.svg).*)'],
}
