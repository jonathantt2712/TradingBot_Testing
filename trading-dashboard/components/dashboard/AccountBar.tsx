'use client'
import { Wallet, TrendingUp, TrendingDown, DollarSign } from 'lucide-react'
import { cn, formatCurrency } from '@/lib/utils'
import type { AlpacaAccount } from '@/lib/alpaca'

interface Props {
  account: AlpacaAccount | null
  error?:  string | null
}

export function AccountBar({ account, error }: Props) {
  if (!account) {
    return (
      <div className="card px-4 py-3 flex items-center gap-2 text-xs text-muted animate-pulse-slow">
        <Wallet className="h-3.5 w-3.5" />
        <span>Account data unavailable — check Alpaca API credentials{error ? `: ${error}` : ''}</span>
      </div>
    )
  }

  const equity      = parseFloat(account.equity)
  const buyingPower = parseFloat(account.buying_power)
  const cash        = parseFloat(account.cash)
  const todayPnl    = equity - parseFloat(account.last_equity)
  const todayPnlPct = parseFloat(account.last_equity) > 0
    ? (todayPnl / parseFloat(account.last_equity)) * 100
    : 0
  const isUp = todayPnl >= 0

  const items = [
    {
      label: 'Portfolio Value',
      value: formatCurrency(equity),
      icon:  Wallet,
      cls:   'text-brand-cyan',
      bg:    'bg-brand-cyan/10',
      mobileHide: false,
    },
    {
      label: "Today's P&L",
      value: `${isUp ? '+' : ''}${formatCurrency(todayPnl)} (${isUp ? '+' : ''}${todayPnlPct.toFixed(2)}%)`,
      icon:  isUp ? TrendingUp : TrendingDown,
      cls:   isUp ? 'text-bull' : 'text-bear',
      bg:    isUp ? 'bg-bull/10' : 'bg-bear/10',
      mobileHide: false,
    },
    {
      label: 'Buying Power',
      value: formatCurrency(buyingPower),
      icon:  DollarSign,
      cls:   'text-caution',
      bg:    'bg-caution/10',
      mobileHide: true,
    },
    {
      label: 'Cash',
      value: formatCurrency(cash),
      icon:  DollarSign,
      cls:   'text-subtle',
      bg:    'bg-bg-elevated',
      mobileHide: true,
    },
  ]

  return (
    <div className="card px-4 py-3 flex flex-wrap items-center gap-3 md:gap-6">
      {/* Paper badge */}
      <span className="rounded-full border border-caution/30 bg-caution/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-caution">
        Paper
      </span>

      {/* Show all on desktop; only first 2 on mobile */}
      {items.map((item) => (
        <div key={item.label} className={cn('flex items-center gap-2', item.mobileHide && 'hidden sm:flex')}>
          <div className={cn('flex h-7 w-7 items-center justify-center rounded-lg', item.bg)}>
            <item.icon className={cn('h-3.5 w-3.5', item.cls)} />
          </div>
          <div>
            <p className="text-[10px] text-muted leading-none">{item.label}</p>
            <p className={cn('text-xs font-bold font-mono mt-0.5', item.cls)}>{item.value}</p>
          </div>
        </div>
      ))}

      {/* Day-trade count */}
      <div className="ml-auto text-right">
        <p className="text-[10px] text-muted">Day Trades</p>
        <p className="text-xs font-mono font-bold text-primary">{account.daytrade_count}/3</p>
        {account.pattern_day_trader && (
          <p className="text-[9px] text-bull hidden sm:block">PDT Account</p>
        )}
      </div>
    </div>
  )
}
