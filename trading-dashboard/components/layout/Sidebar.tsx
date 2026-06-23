'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { signOut } from 'next-auth/react'
import {
  LayoutDashboard, TrendingUp, BarChart2,
  Zap, Settings, LogOut, User, Brain,
} from 'lucide-react'
import { cn } from '@/lib/utils'

// Four purpose-based tabs. `match` lists the child routes a grouped tab owns,
// so the tab highlights for any of its sub-pages (sub-tabs render in the layout).
type NavItem = { href: string; icon: typeof Brain; label: string; match?: string[] }

const nav: NavItem[] = [
  { href: '/',       icon: LayoutDashboard, label: 'Dashboard' },
  { href: '/trades', icon: TrendingUp,      label: 'Trades'    },
  { href: '/agents', icon: Brain,           label: 'Strategy',  match: ['/agents', '/learning'] },
  { href: '/pnl',    icon: BarChart2,       label: 'Analytics', match: ['/pnl', '/history', '/backtest', '/validation'] },
]

function isActive(item: NavItem, path: string): boolean {
  if (item.match) return item.match.some(m => path === m || path.startsWith(m + '/'))
  return item.href === '/' ? path === '/' : path.startsWith(item.href)
}

/** Bottom tab bar shown only on mobile (< md breakpoint) */
export function MobileNav() {
  const path = usePathname()
  return (
    <nav className="md:hidden fixed bottom-0 left-0 right-0 z-50 flex items-center justify-around
                    border-t border-bg-border bg-bg-card/95 backdrop-blur-sm px-2 pb-safe">
      {nav.map((item) => {
        const Icon = item.icon
        const active = isActive(item, path)
        return (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              'flex flex-col items-center gap-0.5 px-2 py-2 rounded-lg text-[10px] font-medium transition-colors min-w-0',
              active ? 'text-brand-cyan' : 'text-muted hover:text-primary',
            )}
          >
            <Icon className="h-5 w-5 shrink-0" />
            <span className="truncate">{item.label}</span>
          </Link>
        )
      })}
      <Link
        href="/profile"
        className={cn(
          'flex flex-col items-center gap-0.5 px-2 py-2 rounded-lg text-[10px] font-medium transition-colors min-w-0',
          path === '/profile' ? 'text-brand-cyan' : 'text-muted hover:text-primary',
        )}
      >
        <User className="h-5 w-5 shrink-0" />
        <span>Profile</span>
      </Link>
      <Link
        href="/settings"
        className={cn(
          'flex flex-col items-center gap-0.5 px-2 py-2 rounded-lg text-[10px] font-medium transition-colors min-w-0',
          path === '/settings' ? 'text-brand-cyan' : 'text-muted hover:text-primary',
        )}
      >
        <Settings className="h-5 w-5 shrink-0" />
        <span>Settings</span>
      </Link>
    </nav>
  )
}

interface SidebarProps { email: string | null }

export function Sidebar({ email }: SidebarProps) {
  const path = usePathname()
  return (
    <aside className="hidden md:flex w-[220px] flex-col border-r border-bg-border bg-bg-card shrink-0">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 py-5 border-b border-bg-border">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-cyan/10 border border-brand-cyan/30">
          <Zap className="h-4 w-4 text-brand-cyan" />
        </div>
        <div>
          <p className="text-sm font-semibold text-primary leading-tight">TradingBot</p>
          <p className="text-[10px] text-muted leading-tight">AI Intelligence</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        <p className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-widest text-muted/60">
          Navigation
        </p>
        {nav.map((item) => {
          const Icon = item.icon
          const active = isActive(item, path)
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-150',
                active
                  ? 'bg-brand-cyan/10 text-brand-cyan border border-brand-cyan/20'
                  : 'text-subtle hover:bg-bg-hover hover:text-primary'
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {item.label}
              {active && (
                <div className="ml-auto h-1.5 w-1.5 rounded-full bg-brand-cyan" />
              )}
            </Link>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="border-t border-bg-border px-3 py-3 space-y-0.5">
        <Link
          href="/profile"
          className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-subtle hover:bg-bg-hover hover:text-primary transition-colors"
        >
          <User className="h-4 w-4 shrink-0" />
          Profile
        </Link>
        <Link
          href="/settings"
          className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-subtle hover:bg-bg-hover hover:text-primary transition-colors"
        >
          <Settings className="h-4 w-4" />
          Settings
        </Link>
        {email && (
          <button
            onClick={() => signOut({ callbackUrl: '/login' })}
            className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-subtle hover:bg-bg-hover hover:text-primary transition-colors"
          >
            <LogOut className="h-4 w-4" />
            Log out
          </button>
        )}
      </div>
    </aside>
  )
}
