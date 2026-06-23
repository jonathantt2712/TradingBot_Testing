'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { cn } from '@/lib/utils'

// Sub-navigation for the grouped top-level tabs. Mounted once in the layout so
// it shows automatically on any child route — no per-page wiring.
const GROUPS: { match: string[]; tabs: { href: string; label: string }[] }[] = [
  {
    match: ['/agents', '/learning'],
    tabs: [
      { href: '/agents',   label: 'Agents'   },
      { href: '/learning', label: 'Learning' },
    ],
  },
  {
    match: ['/pnl', '/history', '/backtest', '/validation'],
    tabs: [
      { href: '/pnl',        label: 'P&L'        },
      { href: '/history',    label: 'History'    },
      { href: '/backtest',   label: 'Backtest'   },
      { href: '/validation', label: 'Validation' },
    ],
  },
]

export function RouteSubTabs() {
  const path = usePathname()
  const group = GROUPS.find(g => g.match.some(m => path === m || path.startsWith(m + '/')))
  if (!group) return null
  return (
    <div className="flex items-center gap-1 px-4 md:px-6 border-b border-bg-border bg-bg-card/40 overflow-x-auto">
      {group.tabs.map(t => {
        const active = path === t.href || path.startsWith(t.href + '/')
        return (
          <Link
            key={t.href}
            href={t.href}
            className={cn(
              'px-3 py-2.5 text-sm font-medium border-b-2 -mb-px whitespace-nowrap transition-colors',
              active ? 'border-brand-cyan text-primary' : 'border-transparent text-muted hover:text-primary',
            )}
          >
            {t.label}
          </Link>
        )
      })}
    </div>
  )
}
