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
                    <div className="flex items-center justify-between mb-0.5">
                      <span className="text-xs font-semibold text-primary">{sig.display}</span>
                      <div className="flex items-center gap-2 text-[10px] text-muted">
                        <span className="font-mono">{sig.raw}</span>
                        {sig.score != null && (
                          <span className={cn('rounded-full border px-1.5 py-0.5 font-mono font-bold', bgColorForScore(sig.score))}>
                            {sig.score.toFixed(0)}
                          </span>
                        )}
                      </div>
                    </div>
                    <p className="text-xs text-subtle">{sig.note}</p>
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
                    <li key={j} className="text-xs text-subtle">{item}</li>
                  ))}
                </ul>
              </div>
            )
          case 'text':
            return (
              <div key={i}>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-muted mb-1">{section.label}</p>
                <p className="text-xs text-subtle leading-relaxed">{section.text}</p>
              </div>
            )
          case 'grid':
            return (
              <div key={i}>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-muted mb-1">{section.label}</p>
                <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
                  {section.entries.map(([k, v]) => (
                    <div key={k} className="rounded-md bg-bg-base px-2 py-1">
                      <p className="text-[9px] text-muted">{k}</p>
                      <p className="font-mono text-xs text-subtle">{v}</p>
                    </div>
                  ))}
                </div>
              </div>
            )
        }
      })}
    </div>
  )
}
