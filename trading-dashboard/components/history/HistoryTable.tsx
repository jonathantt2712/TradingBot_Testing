'use client'
import { useState } from 'react'
import { ArrowUpRight, ArrowDownLeft, ChevronUp, ChevronDown, Search } from 'lucide-react'
import { cn, formatPrice, formatCurrency, formatPct, colorForPnl } from '@/lib/utils'
import type { TradeRecord } from '@/types/trading'

interface Props { trades: TradeRecord[] }

type SortKey = 'opened_at' | 'ticker' | 'pnl' | 'pnl_pct'
type SortDir = 'asc' | 'desc'

function computePnl(t: TradeRecord): number | null {
  if (t.pnl != null) return t.pnl
  if (t.entry && t.exit && t.qty) {
    const mult = t.direction === 'LONG' ? 1 : -1
    return mult * (t.exit - t.entry) * t.qty
  }
  return null
}

function computePnlPct(t: TradeRecord): number | null {
  if (t.pnl_pct != null) return t.pnl_pct
  if (t.entry && t.exit) {
    const mult = t.direction === 'LONG' ? 1 : -1
    return mult * (t.exit - t.entry) / t.entry * 100
  }
  return null
}

function elapsed(opened: string, closed: string | null): string | null {
  if (!closed) return null
  const ms = new Date(closed).getTime() - new Date(opened).getTime()
  if (isNaN(ms) || ms < 0) return null
  const h = Math.floor(ms / 3_600_000)
  const m = Math.floor((ms % 3_600_000) / 60_000)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleDateString('he-IL', { day: '2-digit', month: '2-digit', year: '2-digit', timeZone: 'Asia/Jerusalem' })
      + ' ' + d.toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Jerusalem' })
  } catch { return iso.slice(0, 16) }
}

export function HistoryTable({ trades }: Props) {
  const [search,  setSearch]  = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('opened_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [filter,  setFilter]  = useState<'all' | 'LONG' | 'SHORT' | 'win' | 'loss'>('all')

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const augmented = trades.map(t => ({
    ...t,
    _pnl:    computePnl(t),
    _pnlPct: computePnlPct(t),
    _dur:    t.duration ?? elapsed(t.opened_at, t.closed_at),
  }))

  const filtered = augmented
    .filter(t => t.ticker.toLowerCase().includes(search.toLowerCase()))
    .filter(t => {
      if (filter === 'LONG')  return t.direction === 'LONG'
      if (filter === 'SHORT') return t.direction === 'SHORT'
      if (filter === 'win')   return (t._pnl ?? 0) > 0
      if (filter === 'loss')  return (t._pnl ?? 0) < 0
      return true
    })
    .sort((a, b) => {
      const mult = sortDir === 'asc' ? 1 : -1
      if (sortKey === 'opened_at') return mult * a.opened_at.localeCompare(b.opened_at)
      if (sortKey === 'ticker')    return mult * a.ticker.localeCompare(b.ticker)
      if (sortKey === 'pnl')       return mult * ((a._pnl ?? 0) - (b._pnl ?? 0))
      if (sortKey === 'pnl_pct')   return mult * ((a._pnlPct ?? 0) - (b._pnlPct ?? 0))
      return 0
    })

  const SortIcon = ({ k }: { k: SortKey }) => {
    if (sortKey !== k) return <ChevronUp className="h-3 w-3 opacity-20" />
    return sortDir === 'asc'
      ? <ChevronUp className="h-3 w-3 text-brand-cyan" />
      : <ChevronDown className="h-3 w-3 text-brand-cyan" />
  }

  const totalPnl = filtered.reduce((s, t) => s + (t._pnl ?? 0), 0)
  const wins     = filtered.filter(t => (t._pnl ?? 0) > 0).length
  const winRate  = filtered.length ? (wins / filtered.length * 100) : 0

  return (
    <div className="card">
      <div className="flex flex-wrap items-center gap-3 border-b border-bg-border px-5 py-4">
        <div className="relative flex-1 min-w-[160px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search ticker..."
            className="w-full rounded-lg border border-bg-border bg-bg-base pl-9 pr-3 py-1.5 text-sm text-primary placeholder:text-muted outline-none focus:border-brand-cyan/40"
          />
        </div>

        <div className="flex items-center gap-1">
          {(['all', 'LONG', 'SHORT', 'win', 'loss'] as const).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-medium transition-all',
                filter === f
                  ? f === 'win' ? 'bg-bull/15 text-bull' : f === 'loss' ? 'bg-bear/15 text-bear' : 'bg-brand-cyan/10 text-brand-cyan'
                  : 'text-muted hover:text-subtle',
              )}
            >
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>

        <div className="flex gap-4 text-xs ml-auto">
          <span className="text-muted">
            {filtered.length} trades · Win Rate:{' '}
            <span className={winRate >= 50 ? 'text-bull font-semibold' : 'text-bear font-semibold'}>
              {winRate.toFixed(1)}%
            </span>
          </span>
          <span className="text-muted">
            Total: <span className={cn('font-mono font-semibold', colorForPnl(totalPnl))}>{formatCurrency(totalPnl)}</span>
          </span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-bg-border">
              {[
                { key: 'opened_at', label: 'תאריך'    },
                { key: 'ticker',    label: 'Ticker'   },
                { key: null,        label: 'Dir'      },
                { key: null,        label: 'Entry'    },
                { key: null,        label: 'Exit'     },
                { key: null,        label: 'Qty'      },
                { key: 'pnl',       label: 'P&L $'    },
                { key: 'pnl_pct',   label: 'P&L %'    },
                { key: null,        label: 'Duration' },
                { key: null,        label: 'Status'   },
              ].map(({ key, label }) => (
                <th
                  key={label}
                  onClick={() => key && toggleSort(key as SortKey)}
                  className={cn(
                    'px-4 py-3 text-left font-medium text-muted',
                    key ? 'cursor-pointer hover:text-subtle select-none' : '',
                  )}
                >
                  <span className="flex items-center gap-1">
                    {label}
                    {key && <SortIcon k={key as SortKey} />}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((t, i) => (
              <tr key={t.id + i} className="border-b border-bg-border/50 hover:bg-bg-hover transition-colors">
                <td className="px-4 py-3 text-muted whitespace-nowrap">{fmtDate(t.opened_at)}</td>
                <td className="px-4 py-3 font-mono font-semibold text-primary">{t.ticker}</td>
                <td className="px-4 py-3">
                  <span className={cn(
                    'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[10px] font-semibold',
                    t.direction === 'LONG' ? 'bg-bull/10 text-bull' : 'bg-bear/10 text-bear',
                  )}>
                    {t.direction === 'LONG'
                      ? <ArrowUpRight className="h-2.5 w-2.5" />
                      : <ArrowDownLeft className="h-2.5 w-2.5" />
                    }
                    {t.direction}
                  </span>
                </td>
                <td className="px-4 py-3 font-mono text-subtle">{formatPrice(t.entry)}</td>
                <td className="px-4 py-3 font-mono text-subtle">{t.exit ? formatPrice(t.exit) : '—'}</td>
                <td className="px-4 py-3 font-mono text-subtle">{t.qty}</td>
                <td className={cn('px-4 py-3 font-mono font-semibold', colorForPnl(t._pnl ?? 0))}>
                  {t._pnl != null ? formatCurrency(t._pnl) : '—'}
                </td>
                <td className={cn('px-4 py-3 font-mono font-semibold', colorForPnl(t._pnlPct ?? 0))}>
                  {t._pnlPct != null ? formatPct(t._pnlPct) : '—'}
                </td>
                <td className="px-4 py-3 text-muted">{t._dur ?? '—'}</td>
                <td className="px-4 py-3">
                  <span className={cn(
                    'rounded-full px-2 py-0.5 text-[10px] font-medium',
                    t.status === 'open'   ? 'bg-brand-cyan/10 text-brand-cyan' :
                    t.status === 'closed' ? 'bg-bg-hover text-muted' :
                    'bg-bear/10 text-bear',
                  )}>
                    {t.status}
                  </span>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={10} className="px-4 py-12 text-center text-muted">
                  No trades match your filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
