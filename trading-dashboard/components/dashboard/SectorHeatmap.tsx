'use client'
import { cn } from '@/lib/utils'
import type { SectorStat } from '@/types/trading'

interface Props { sectors: SectorStat[] }

function scoreToColor(score: number): string {
  if (score >= 70) return 'bg-bull/20 border-bull/30 text-bull'
  if (score >= 60) return 'bg-bull/10 border-bull/20 text-bull/80'
  if (score >= 50) return 'bg-caution/10 border-caution/20 text-caution'
  if (score >= 40) return 'bg-bear/10 border-bear/20 text-bear/80'
  return 'bg-bear/20 border-bear/30 text-bear'
}

export function SectorHeatmap({ sectors }: Props) {
  const sorted = [...sectors].sort((a, b) => b.score - a.score)

  return (
    <div className="card p-4">
      <p className="stat-label mb-3">Sector Heat</p>
      <div className="grid grid-cols-2 gap-1.5">
        {sorted.map(s => (
          <div
            key={s.sector}
            className={cn(
              'rounded-lg border px-3 py-2 transition-all duration-200 hover:scale-[1.02] cursor-default',
              scoreToColor(s.score)
            )}
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium truncate">{s.sector}</span>
              <span className={cn('text-xs font-mono font-bold', (s.change ?? 0) >= 0 ? 'text-bull' : 'text-bear')}>
                {(s.change ?? 0) >= 0 ? '+' : ''}{(s.change ?? 0).toFixed(1)}%
              </span>
            </div>
            <div className="mt-1">
              <div className="score-bar-track">
                <div
                  className={cn('h-full rounded-full transition-all duration-500', s.score >= 50 ? 'bg-bull/60' : 'bg-bear/60')}
                  style={{ width: `${s.score}%` }}
                />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
