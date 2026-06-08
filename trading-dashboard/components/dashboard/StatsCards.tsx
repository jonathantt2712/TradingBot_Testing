'use client'
import { TrendingUp, TrendingDown, Activity, Target, ShieldCheck, BarChart2 } from 'lucide-react'
import { cn, formatCurrency, formatPct } from '@/lib/utils'
import type { PortfolioStats } from '@/types/trading'

interface Props { stats: PortfolioStats }

export function StatsCards({ stats }: Props) {
  const cards = [
    {
      label:   'Total P&L',
      value:   formatCurrency(stats.total_pnl),
      sub:     `Today ${formatCurrency(stats.today_pnl)}`,
      icon:    stats.total_pnl >= 0 ? TrendingUp : TrendingDown,
      accent:  stats.total_pnl >= 0 ? 'bull' : 'bear',
    },
    {
      label:   'Win Rate',
      value:   `${stats.win_rate.toFixed(1)}%`,
      sub:     `${stats.total_trades} total trades`,
      icon:    Target,
      accent:  stats.win_rate >= 50 ? 'bull' : 'bear',
    },
    {
      label:   'Sharpe Ratio',
      value:   stats.sharpe_ratio.toFixed(2),
      sub:     `Max DD ${formatPct(stats.max_drawdown)}`,
      icon:    Activity,
      accent:  stats.sharpe_ratio >= 1 ? 'bull' : 'caution',
    },
    {
      label:   'Open Positions',
      value:   stats.open_positions.toString(),
      sub:     `Avg R/R ${stats.avg_rr.toFixed(2)}`,
      icon:    ShieldCheck,
      accent:  'cyan',
    },
  ] as const

  const accentMap = {
    bull:    { text: 'text-bull',    bg: 'bg-bull/10',    border: 'border-bull/20'   },
    bear:    { text: 'text-bear',    bg: 'bg-bear/10',    border: 'border-bear/20'   },
    caution: { text: 'text-caution', bg: 'bg-caution/10', border: 'border-caution/20'},
    cyan:    { text: 'text-brand-cyan', bg: 'bg-brand-cyan/10', border: 'border-brand-cyan/20' },
  }

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {cards.map((c) => {
        const a = accentMap[c.accent as keyof typeof accentMap]
        return (
          <div key={c.label} className="card p-5 animate-slide-up">
            <div className="flex items-start justify-between">
              <div>
                <p className="stat-label">{c.label}</p>
                <p className={cn('mt-1 text-2xl font-bold tabular-nums', a.text)}>{c.value}</p>
                <p className="mt-1 text-xs text-muted">{c.sub}</p>
              </div>
              <div className={cn('flex h-9 w-9 items-center justify-center rounded-lg border', a.bg, a.border)}>
                <c.icon className={cn('h-4 w-4', a.text)} />
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
