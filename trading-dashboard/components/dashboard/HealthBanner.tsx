'use client'
import { useState } from 'react'
import { AlertTriangle, AlertCircle, X } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface HealthIssue {
  key:         string
  message:     string
  remediation: string
  severity:    string   // 'error' | 'warning'
  count:       number
}

/** Renders a single warning icon. Click opens a modal listing all issues. */
export function HealthBanner({ issues }: { issues: HealthIssue[] }) {
  const [open, setOpen] = useState(false)
  if (!issues || issues.length === 0) return null

  const hasError = issues.some(i => i.severity === 'error')

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Needs attention"
        className={cn(
          'relative flex items-center justify-center h-7 w-7 rounded-lg border transition-colors',
          hasError
            ? 'border-bear/40 bg-bear/10 text-bear hover:bg-bear/20'
            : 'border-caution/40 bg-caution/10 text-caution hover:bg-caution/20',
        )}
      >
        <AlertTriangle className="h-3.5 w-3.5" />
        <span className={cn(
          'absolute -top-1.5 -right-1.5 flex h-3.5 w-3.5 items-center justify-center rounded-full text-[9px] font-bold text-white',
          hasError ? 'bg-bear' : 'bg-caution',
        )}>
          {issues.length}
        </span>
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
          onClick={() => setOpen(false)}
        >
          <div
            className="relative w-full max-w-md rounded-2xl border border-bg-border bg-bg-card p-6 shadow-2xl"
            onClick={e => e.stopPropagation()}
          >
            <button
              onClick={() => setOpen(false)}
              className="absolute right-4 top-4 text-muted hover:text-primary transition-colors"
            >
              <X className="h-4 w-4" />
            </button>

            <div className="flex items-center gap-2 mb-4">
              <AlertTriangle className={cn('h-4 w-4 shrink-0', hasError ? 'text-bear' : 'text-caution')} />
              <h2 className="text-base font-bold text-primary">Needs Attention</h2>
              <span className="text-[11px] text-muted">
                {issues.length} issue{issues.length > 1 ? 's' : ''}
              </span>
            </div>

            <ul className="space-y-3">
              {issues.map(issue => {
                const isError = issue.severity === 'error'
                const Icon    = isError ? AlertCircle : AlertTriangle
                return (
                  <li key={issue.key} className={cn(
                    'flex items-start gap-3 rounded-lg border p-3 text-sm',
                    isError
                      ? 'border-bear/20 bg-bear/5'
                      : 'border-caution/20 bg-caution/5',
                  )}>
                    <Icon className={cn('h-4 w-4 mt-0.5 shrink-0', isError ? 'text-bear' : 'text-caution')} />
                    <div className="leading-snug space-y-0.5">
                      <p className={cn('font-semibold', isError ? 'text-bear' : 'text-caution')}>
                        {issue.message}
                      </p>
                      {issue.remediation && (
                        <p className="text-xs text-muted">{issue.remediation}</p>
                      )}
                    </div>
                  </li>
                )
              })}
            </ul>
          </div>
        </div>
      )}
    </>
  )
}
