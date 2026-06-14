import type { Metadata } from 'next'
import './globals.css'
import { Sidebar, MobileNav } from '@/components/layout/Sidebar'
import { SessionProvider } from '@/components/providers/SessionProvider'
import { Toaster } from 'sonner'
import { auth } from '@/auth'

export const metadata: Metadata = {
  title: 'Trading Bot Dashboard',
  description: 'AI-powered multi-agent trading intelligence',
  icons: {
    icon: '/favicon.svg',
    shortcut: '/favicon.svg',
  },
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const session = await auth()
  const email = session?.user?.email ?? null

  const toaster = (
    <Toaster
      theme="dark"
      toastOptions={{
        style: {
          background: '#0F172A',
          border: '1px solid #1E293B',
          color: '#F1F5F9',
        },
      }}
    />
  )

  // Unauthenticated requests are redirected to /login by middleware, so a
  // missing session means this is the login page — render it without the
  // dashboard chrome.
  if (!email) {
    return (
      <html lang="en" className="dark">
        <body className="h-dvh bg-bg-base text-primary">
          <SessionProvider>
            {children}
            {toaster}
          </SessionProvider>
        </body>
      </html>
    )
  }

  return (
    <html lang="en" className="dark">
      <body className="flex h-dvh overflow-hidden bg-bg-base text-primary">
        <SessionProvider>
          <Sidebar email={email} />
          <main className="flex-1 overflow-y-auto pb-16 md:pb-0">
            {children}
          </main>
          <MobileNav />
          {toaster}
        </SessionProvider>
      </body>
    </html>
  )
}
