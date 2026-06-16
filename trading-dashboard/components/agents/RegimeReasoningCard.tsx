import { Globe2 } from 'lucide-react'
import { cn, regimeLabel, regimeColor } from '@/lib/utils'
import type { RegimeInfo } from '@/types/trading'

interface Props {
  regime: RegimeInfo
}

export function RegimeReasoningCard({ regime }: Props) {
  const reasoning = regime.reasoning
  const inputs = reasoning?.inputs as Record<string, any> | undefined
  const rules  = reasoning?.rules  as Record<string, string> | undefined
  const activeRule = rules?.[regime.regime]

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Globe2 className="h-4 w-4 text-brand-cyan" />
          <h2 className="text-sm font-semibold text-primary">Market Regime</h2>
        </div>
        <span className={cn('rounded-full border px-3 py-1 text-xs font-bold', regimeColor(regime.regime))}>
          {regimeLabel(regime.regime)}
        </span>
      </div>

      <p className="text-sm text-subtle">{regime.rationale}</p>

      {inputs && (
        <div className="grid grid-cols-3 gap-2">
          {[
            { label: 'VIX',      value: inputs.vix },
            { label: 'SPY chg',  value: typeof inputs.spy_day_chg_pct === 'number' ? `${inputs.spy_day_chg_pct >= 0 ? '+' : ''}${inputs.spy_day_chg_pct.toFixed(2)}%` : inputs.spy_day_chg_pct },
            { label: 'QQQ chg',  value: typeof inputs.qqq_day_chg_pct === 'number' ? `${inputs.qqq_day_chg_pct >= 0 ? '+' : ''}${inputs.qqq_day_chg_pct.toFixed(2)}%` : inputs.qqq_day_chg_pct },
          ].map(({ label, value }) => (
            <div key={label} className="rounded-lg bg-bg-base p-2 text-center">
              <p className="text-[10px] text-muted">{label}</p>
              <p className="font-mono text-sm font-bold text-primary">{value}</p>
            </div>
          ))}
        </div>
      )}

      {activeRule && (
        <div className="rounded-lg border border-bg-border bg-bg-base px-3 py-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted mb-1">
            Why {regimeLabel(regime.regime)}
          </p>
          <p className="text-xs text-subtle">{activeRule}</p>
        </div>
      )}
    </div>
  )
}
