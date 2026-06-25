'use client'
import { useState, useEffect } from 'react'
import { X, AlertTriangle, Sliders, Lock, Bot, Info, Loader2, Save, RotateCcw } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'

interface Weights {
  min_score:           number
  atr_stop_multiple:   number
  atr_target_multiple: number
  time_window_minutes: number
  win_rate_30d:        number | null
  update_count:        number
  manual_overrides:    Record<string, boolean>
  override_expires_at: string | null
  defaults: {
    min_score:           number
    atr_stop_multiple:   number
    atr_target_multiple: number
    time_window_minutes: number
  }
}

const FIELDS: {
  key:         string
  label:       string
  unit:        string
  min:         number
  max:         number
  step:        number
  description: string
  impact:      string
  tip:         string
}[] = [
  {
    key:   'min_score',
    label: 'Min Score to Trade',
    unit:  'pts',
    min:   20, max: 70, step: 1,
    description: 'Minimum composite score (out of 100) a ticker must reach before the bot considers it for a trade.',
    impact:      'Lower → more trades, less selective. Higher → fewer trades, higher conviction only.',
    tip:         'Default 40. If the bot takes too many bad trades, raise to 50–55. If it barely trades, lower to 35.',
  },
  {
    key:   'atr_stop_multiple',
    label: 'ATR Stop Multiple',
    unit:  '×',
    min:   1.0, max: 4.0, step: 0.1,
    description: 'Stop-loss is placed at this many ATRs (average true range) away from the entry price.',
    impact:      'Lower (1.5) → tight stop, more small losses from noise. Higher (3.0) → wider stop, larger individual losses but fewer false exits.',
    tip:         'Default 2.0. In volatile markets raise to 2.5–3.0. In quiet markets 1.5–2.0 works well.',
  },
  {
    key:   'atr_target_multiple',
    label: 'ATR Target Multiple',
    unit:  '×',
    min:   1.5, max: 6.0, step: 0.1,
    description: 'Take-profit target is placed at this many ATRs from the entry price.',
    impact:      'Together with the stop multiple this defines the R/R ratio. e.g. Stop×2 + Target×3 = 1.5× R/R. Higher target → fewer winners but each win is larger.',
    tip:         'Default 3.0. Always keep this larger than the stop multiple. Recommended minimum ratio: 1.5× the stop.',
  },
  {
    key:   'time_window_minutes',
    label: 'Signal Window',
    unit:  'min',
    min:   15, max: 120, step: 5,
    description: 'How long a trade recommendation stays active before it expires and is re-evaluated or dropped.',
    impact:      'Lower → recommendations refresh faster, bot adapts to market changes quickly. Higher → signals stay longer, less churn but may hold stale data.',
    tip:         'Default 45 min. During fast-moving opens or news events, lower to 20–30. Normal sessions: 45–60.',
  },
]

function FieldRow({
  field, value, defaultValue, isLocked, onChange,
}: {
  field:        typeof FIELDS[number]
  value:        number
  defaultValue: number
  isLocked:     boolean
  onChange:     (v: number) => void
}) {
  const [showInfo, setShowInfo] = useState(false)
  const isDefault = Math.abs(value - defaultValue) < 0.001

  return (
    <div className={cn(
      'rounded-xl border p-4 space-y-3 transition-colors',
      isLocked ? 'border-brand-cyan/30 bg-brand-cyan/5' : 'border-bg-border bg-bg-base',
    )}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {isLocked
            ? <Lock className="h-3.5 w-3.5 text-brand-cyan shrink-0" />
            : <Bot  className="h-3.5 w-3.5 text-muted shrink-0" />
          }
          <span className="text-sm font-semibold text-primary">{field.label}</span>
          <button
            onClick={() => setShowInfo(v => !v)}
            className="text-muted hover:text-subtle transition-colors"
          >
            <Info className="h-3.5 w-3.5" />
          </button>
        </div>
        {!isDefault && (
          <button
            onClick={() => onChange(defaultValue)}
            className="text-[10px] text-muted hover:text-subtle transition-colors"
          >
            ↩ default ({defaultValue}{field.unit})
          </button>
        )}
      </div>

      {showInfo && (
        <div className="rounded-lg bg-bg-elevated border border-bg-border px-3 py-2.5 space-y-1.5 text-xs">
          <p className="text-subtle leading-relaxed">{field.description}</p>
          <p className="text-muted leading-relaxed">
            <span className="text-caution font-medium">Effect: </span>{field.impact}
          </p>
          <p className="text-muted leading-relaxed">
            <span className="text-brand-cyan font-medium">Tip: </span>{field.tip}
          </p>
        </div>
      )}

      <div className="flex items-center gap-3">
        <input
          type="range"
          min={field.min} max={field.max} step={field.step}
          value={value}
          onChange={e => onChange(parseFloat(e.target.value))}
          className="flex-1 accent-brand-cyan"
        />
        <div className="flex items-center gap-1 shrink-0">
          <input
            type="number"
            min={field.min} max={field.max} step={field.step}
            value={value}
            onChange={e => {
              const v = parseFloat(e.target.value)
              if (!isNaN(v) && v >= field.min && v <= field.max) onChange(v)
            }}
            className="w-16 rounded-md border border-bg-border bg-bg-elevated px-2 py-1 text-xs font-mono text-primary text-right focus:outline-none focus:border-brand-cyan/50"
          />
          <span className="text-[10px] text-muted w-6">{field.unit}</span>
        </div>
      </div>

      <p className="text-[10px] text-muted">
        {isLocked
          ? 'Manually set — self-tuner will not change this value today'
          : 'Managed automatically by the self-tuner'
        }
      </p>
    </div>
  )
}

interface Props {
  onClose: () => void
}

export function StrategyTuningModal({ onClose }: Props) {
  const [step,      setStep]      = useState<'warning' | 'edit'>('warning')
  const [weights,   setWeights]   = useState<Weights | null>(null)
  const [draft,     setDraft]     = useState<Record<string, number>>({})
  const [loading,   setLoading]   = useState(true)
  const [saving,    setSaving]    = useState(false)
  const [resetting, setResetting] = useState(false)

  async function load() {
    try {
      const r = await fetch('/api/optimize/patch', { cache: 'no-store' })
      if (!r.ok) return
      const w: Weights = await r.json()
      setWeights(w)
      setDraft({
        min_score:           w.min_score,
        atr_stop_multiple:   w.atr_stop_multiple,
        atr_target_multiple: w.atr_target_multiple,
        time_window_minutes: w.time_window_minutes,
      })
    } catch {}
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  function handleChange(key: string, val: number) {
    setDraft(d => ({ ...d, [key]: val }))
  }

  async function save() {
    setSaving(true)
    try {
      // Send all four values — each gets locked since it's a manual session
      const r = await fetch('/api/optimize/patch', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(draft),
      })
      if (!r.ok) throw new Error()
      toast.success('Parameters saved — self-tuner will continue adjusting from these values')
      onClose()
    } catch {
      toast.error('Could not save — bot offline?')
    } finally {
      setSaving(false)
    }
  }

  async function resetAll() {
    setResetting(true)
    try {
      const r = await fetch('/api/optimize/reset', { method: 'POST' })
      if (!r.ok) throw new Error()
      toast.success('Weights reset to defaults')
      onClose()
    } catch {
      toast.error('Could not reset — bot offline?')
    } finally {
      setResetting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="relative w-full max-w-lg rounded-2xl border border-bg-border bg-bg-elevated shadow-card flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-bg-border shrink-0">
          <div className="flex items-center gap-2">
            <Sliders className="h-4 w-4 text-brand-cyan" />
            <h2 className="text-sm font-semibold text-primary">Change Strategy Parameters</h2>
          </div>
          <button onClick={onClose} className="text-muted hover:text-primary transition-colors">
            <X className="h-4 w-4" />
          </button>
        </div>

        {step === 'warning' ? (
          /* Step 1: Warning */
          <div className="px-6 py-5 space-y-4">
            <div className="flex gap-3 rounded-xl border border-caution/30 bg-caution/10 p-4">
              <AlertTriangle className="h-5 w-5 text-caution shrink-0 mt-0.5" />
              <div className="space-y-2 text-sm">
                <p className="font-semibold text-caution">Manual parameter seed</p>
                <p className="text-subtle leading-relaxed">
                  The values you set here become the bot&apos;s <strong className="text-primary">new starting point</strong>.
                  The self-tuner will continue running and may adjust them further over time
                  based on trade results — just as it would from the defaults.
                </p>
                <p className="text-subtle leading-relaxed">
                  Use this when you want to guide the bot in a specific direction
                  (e.g. be more conservative in a choppy market) without turning off
                  the automatic learning entirely.
                </p>
              </div>
            </div>

            <div className="flex gap-2 pt-1">
              <button onClick={onClose} className="btn-ghost text-xs flex-1">Cancel</button>
              <button
                onClick={() => setStep('edit')}
                className="btn-primary text-xs flex-1"
              >
                Got it — set parameters
              </button>
            </div>
          </div>
        ) : (
          /* Step 2: Edit */
          <>
            <div className="px-6 py-4 overflow-y-auto flex-1 space-y-3">
              {loading || !weights ? (
                <div className="flex items-center justify-center py-8 gap-2 text-sm text-muted">
                  <Loader2 className="h-4 w-4 animate-spin" /> Loading current values…
                </div>
              ) : (
                <>
                  {/* Self-tuner status strip */}
                  {weights.win_rate_30d !== null && (
                    <div className="rounded-lg bg-bg-base border border-bg-border px-3 py-2 flex items-center justify-between text-xs text-muted">
                      <span>Self-tuner: win rate {weights.win_rate_30d}% over last {weights.update_count} updates</span>
                      <span className={cn(
                        'font-medium',
                        weights.win_rate_30d >= 50 ? 'text-bull' : 'text-bear',
                      )}>
                        {weights.win_rate_30d >= 60 ? 'good' : weights.win_rate_30d >= 40 ? 'mixed' : 'struggling'}
                      </span>
                    </div>
                  )}

                  {FIELDS.map(field => (
                    <FieldRow
                      key={field.key}
                      field={field}
                      value={draft[field.key] ?? (weights as any)[field.key]}
                      defaultValue={(weights.defaults as any)[field.key]}
                      isLocked={true}
                      onChange={v => handleChange(field.key, v)}
                    />
                  ))}
                </>
              )}
            </div>

            {/* Footer */}
            <div className="px-6 py-4 border-t border-bg-border flex items-center justify-between gap-3 shrink-0">
              <button
                onClick={resetAll}
                disabled={resetting}
                className="flex items-center gap-1.5 text-xs text-muted hover:text-subtle transition-colors disabled:opacity-50"
              >
                {resetting
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  : <RotateCcw className="h-3.5 w-3.5" />
                }
                Reset to defaults
              </button>

              <div className="flex gap-2">
                <button onClick={onClose} className="btn-ghost text-xs">Cancel</button>
                <button
                  onClick={save}
                  disabled={saving || !weights}
                  className="btn-primary text-xs disabled:opacity-50 flex items-center gap-1.5"
                >
                  {saving
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    : <Save className="h-3.5 w-3.5" />
                  }
                  Save
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
