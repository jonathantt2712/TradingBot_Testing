import { AlertTriangle } from 'lucide-react'
import { cn, bgColorForScore } from '@/lib/utils'
import { reasoningToSections } from '@/lib/reasoning'

interface Props {
  reasoning?: Record<string, any> | null
}

/** Renders a per-agent `reasoning` dict as a stack of labeled sections. */
export function ReasoningDetail({ reasoning }: Props) {
  const sections = reasoningToSections(reasoning)
  if (sections.length === 0) return null

  return (
    <div className="space-y-2">
      {sections.map((section, i) => {
        switch (section.type) {
          case 'warning':
            return (
              <div key={i} className="flex items-center gap-2 rounded-lg border border-bear/30 bg-bear/10 px-3 py-2 text-xs text-bear">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                <span>{section.text}</span>
              </div>
            )
          case 'signals':
            return (
              <div key={i} className="space-y-1.5">
                {section.signals.map(sig => (
                  <div key={sig.name} className="rounded-lg border border-bg-border bg-bg-base px-3 py-2">
                    <div className="flex items-start justify-between gap-2 mb-1">
                      <span className="text-xs font-semibold text-primary min-w-0 leading-tight">{sig.display}</span>
                      <div className="flex items-center gap-1.5 text-[10px] text-muted shrink-0">
                        <span className="font-mono text-subtle max-w-[7rem] truncate" title={sig.raw}>{sig.raw}</span>
                        {sig.score != null && (
                          <span className={cn('rounded-full border px-1.5 py-0.5 font-mono font-bold', bgColorForScore(sig.score))}>
                            {sig.score.toFixed(0)}
                          </span>
                        )}
                      </div>
                    </div>
                    <p className="text-[10px] text-muted leading-relaxed">{sig.note}</p>
                  </div>
                ))}
              </div>
            )
          case 'list':
            return (
              <div key={i}>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-muted mb-1">{section.label}</p>
                <ul className="list-disc pl-4 space-y-0.5">
                  {section.items.map((item, j) => (
                    <li key={j} className="text-xs text-subtle break-words">{item}</li>
                  ))}
                </ul>
              </div>
            )
          case 'text':
            return (
              <div key={i}>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-muted mb-1">{section.label}</p>
                <p className="text-xs text-subtle leading-relaxed break-words">{section.text}</p>
              </div>
            )
          case 'grid': {
            // Separate short values (numbers, short strings) from long text values.
            // Long values overflow grid cells, so render them as a vertical key-value list instead.
            const short: [string, string][] = []
            const long:  [string, string][] = []
            for (const [k, v] of section.entries) {
              ;(v.length > 30 ? long : short).push([k, v])
            }
            return (
              <div key={i} className="space-y-1.5">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-muted">{section.label}</p>
                {short.length > 0 && (
                  <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
                    {short.map(([k, v]) => (
                      <div key={k} className="rounded-md bg-bg-base px-2 py-1 overflow-hidden">
                        <p className="text-[9px] text-muted truncate">{k}</p>
                        <p className="font-mono text-xs text-subtle break-all">{v}</p>
                      </div>
                    ))}
                  </div>
                )}
                {long.length > 0 && (
                  <div className="space-y-1">
                    {long.map(([k, v]) => (
                      <div key={k} className="rounded-md bg-bg-base px-2 py-1.5">
                        <p className="text-[9px] font-medium text-muted mb-0.5">{k}</p>
                        <p className="text-xs text-subtle break-words leading-relaxed">{v}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          }
        }
      })}
    </div>
  )
}
