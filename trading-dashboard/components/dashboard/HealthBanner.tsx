import { AlertTriangle, AlertCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface HealthIssue {
  key:         string
  message:     string
  remediation: string
  severity:    string   // 'error' | 'warning'
  count:       number
}

/** "Needs attention" banner — surfaces what the bot needs from the operator
 *  (a rejected API key, no account equity, a failing agent). Renders nothing
 *  when everything is healthy. */
export function HealthBanner({ issues }: { issues: HealthIssue[] }) {
  if (!issues || issues.length === 0) return null

  return (
    <div className="card border-caution/30 px-4 py-3 space-y-2">
      <div className="flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 text-caution shrink-0" />
        <h2 className="text-sm font-bold text-primary">Needs attention</h2>
        <span className="text-[10px] text-muted">
          {issues.length} issue{issues.length > 1 ? 's' : ''}
        </span>
      </div>
      <ul className="space-y-1.5">
        {issues.map((issue) => {
          const isError = issue.severity === 'error'
          const Icon = isError ? AlertCircle : AlertTriangle
          return (
            <li key={issue.key} className="flex items-start gap-2 text-xs">
              <Icon className={cn('h-3.5 w-3.5 mt-0.5 shrink-0', isError ? 'text-bear' : 'text-caution')} />
              <div className="leading-snug">
                <span className={cn('font-semibold', isError ? 'text-bear' : 'text-caution')}>
                  {issue.message}
                </span>
                {issue.remediation && <span className="text-muted"> — {issue.remediation}</span>}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
