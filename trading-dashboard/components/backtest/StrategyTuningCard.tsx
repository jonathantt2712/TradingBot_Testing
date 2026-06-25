'use client'
import { useState, useEffect } from 'react'
import { Sliders, RotateCcw, Save, Lock, Bot, Loader2, Info } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'

interface Weights {
  min_score:           number
  atr_stop_multiple:   number
  atr_target_multiple: number
  time_window_minutes: number
  win_rate_30d:        number | null
  update_count:        number
  bias:                string
  last_updated:        string | null
  manual_overrides:    Record<string, boolean>
  defaults: {
    min_score:           number
    atr_stop_multiple:   number
    atr_target_multiple: number
    time_window_minutes: number
  }
}

const FIELD_META: Record<string, {
  label:       string
  unit:        string
  min:         number
  max:         number
  step:        number
  description: string
  impact:      string
  tip:         string
}> = {
  min_score: {
    label:       'Min Score to Trade',
    unit:        'pts',
    min:         20,
    max:         70,
    step:        1,
    description: 'הסף המינימלי שטיקר צריך לקבל (מ-100) כדי שהבוט בכלל יציע עסקה עליו.',
    impact:      'ערך נמוך → יותר עסקאות (פחות סלקטיבי). ערך גבוה → פחות עסקאות אבל בטוחות יותר.',
    tip:         'ברירת מחדל 40. אם הבוט מציע יותר מדי עסקאות גרועות — העלה ל-50-55. אם הוא לא מציע כלום — הורד ל-35.',
  },
  atr_stop_multiple: {
    label:       'ATR Stop Multiple',
    unit:        '×',
    min:         1.0,
    max:         4.0,
    step:        0.1,
    description: 'כמה פעמים ה-ATR (טווח יומי ממוצע) מגדיר את מרחק הסטופ-לוס מנקודת הכניסה.',
    impact:      'ערך נמוך (1.5) → סטופ צמוד, יותר פגיעות, יותר הפסדים קטנים. ערך גבוה (3.0) → סטופ רחוק, הפסד יחיד גדול יותר אבל פחות "נסיעות פיקטיביות".',
    tip:         'ברירת מחדל 2.0. בשוק תנודתי — הגדל ל-2.5-3.0. בשוק שקט — 1.5-2.0.',
  },
  atr_target_multiple: {
    label:       'ATR Target Multiple',
    unit:        '×',
    min:         1.5,
    max:         6.0,
    step:        0.1,
    description: 'כמה פעמים ה-ATR מגדיר את יעד הרווח (Take Profit) מנקודת הכניסה.',
    impact:      'יחס זה ל-ATR Stop יוצר את ה-R/R Ratio. למשל Stop×2 + Target×3 = R/R של 1.5x. ערך גבוה → פחות עסקאות מגיעות ליעד אבל כל זכייה שווה יותר.',
    tip:         'ברירת מחדל 3.0. תמיד צריך להיות גדול מה-Stop Multiple. יחס מינימלי מומלץ: 1.5x יחס לסטופ.',
  },
  time_window_minutes: {
    label:       'Signal Window',
    unit:        'min',
    min:         15,
    max:         120,
    step:        5,
    description: 'כמה דקות המלצה נשארת פעילה לפני שפג תוקפה (ונבדקת מחדש או נמחקת).',
    impact:      'ערך נמוך → המלצות מתחלפות מהר, הבוט "מתעדכן" לשוק. ערך גבוה → המלצות נשארות יותר, פחות רעש אבל עלול להחזיק אות ישן.',
    tip:         'ברירת מחדל 45 דקות. בשוק מהיר (פתיחה, חדשות) — הורד ל-20-30. בשוק רגיל — 45-60.',
  },
}

function FieldRow({
  fieldKey,
  value,
  defaultValue,
  isLocked,
  onChange,
  onUnlock,
}: {
  fieldKey:     string
  value:        number
  defaultValue: number
  isLocked:     boolean
  onChange:     (v: number) => void
  onUnlock:     () => void
}) {
  const meta    = FIELD_META[fieldKey]
  const [show, setShow] = useState(false)
  const isDefault = Math.abs(value - defaultValue) < 0.001

  return (
    <div className={cn(
      'rounded-xl border p-4 space-y-3 transition-colors',
      isLocked ? 'border-brand-cyan/30 bg-brand-cyan/5' : 'border-bg-border bg-bg-base',
    )}>
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          {isLocked && <Lock className="h-3.5 w-3.5 text-brand-cyan shrink-0" />}
          <span className="text-sm font-semibold text-primary">{meta.label}</span>
          <button onClick={() => setShow(v => !v)} className="text-muted hover:text-subtle transition-colors">
            <Info className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!isDefault && (
            <button
              onClick={() => onChange(defaultValue)}
              className="text-[10px] text-muted hover:text-subtle transition-colors"
              title={`Reset to default (${defaultValue}${meta.unit})`}
            >
              ↩ {defaultValue}{meta.unit}
            </button>
          )}
          {isLocked && (
            <button
              onClick={onUnlock}
              className="text-[10px] text-caution hover:text-caution/80 transition-colors"
              title="Remove manual lock — let self-tuner manage this"
            >
              Unlock
            </button>
          )}
        </div>
      </div>

      {/* Info panel */}
      {show && (
        <div className="rounded-lg bg-bg-elevated border border-bg-border px-3 py-2 space-y-1.5">
          <p className="text-xs text-subtle leading-relaxed">{meta.description}</p>
          <p className="text-xs text-muted leading-relaxed"><span className="text-caution font-medium">השפעה:</span> {meta.impact}</p>
          <p className="text-xs text-muted leading-relaxed"><span className="text-brand-cyan font-medium">טיפ:</span> {meta.tip}</p>
        </div>
      )}

      {/* Slider + value */}
      <div className="flex items-center gap-3">
        <input
          type="range"
          min={meta.min}
          max={meta.max}
          step={meta.step}
          value={value}
          onChange={e => onChange(parseFloat(e.target.value))}
          className="flex-1 accent-brand-cyan"
        />
        <div className="flex items-center gap-1 w-20 shrink-0">
          <input
            type="number"
            min={meta.min}
            max={meta.max}
            step={meta.step}
            value={value}
            onChange={e => {
              const v = parseFloat(e.target.value)
              if (!isNaN(v) && v >= meta.min && v <= meta.max) onChange(v)
            }}
            className="w-14 rounded-md border border-bg-border bg-bg-elevated px-2 py-1 text-xs font-mono text-primary text-right focus:outline-none focus:border-brand-cyan/50"
          />
          <span className="text-[10px] text-muted">{meta.unit}</span>
        </div>
      </div>

      {/* Self-tuner status */}
      <p className="text-[10px] text-muted flex items-center gap-1">
        {isLocked
          ? <><Lock className="h-3 w-3 text-brand-cyan" /> ערך נעול ידנית — הself-tuner לא ישנה זאת</>
          : <><Bot  className="h-3 w-3" /> מנוהל על-ידי הself-tuner</>
        }
      </p>
    </div>
  )
}

export function StrategyTuningCard() {
  const [weights,  setWeights]  = useState<Weights | null>(null)
  const [draft,    setDraft]    = useState<Record<string, number>>({})
  const [locked,   setLocked]   = useState<Record<string, boolean>>({})
  const [loading,  setLoading]  = useState(true)
  const [saving,   setSaving]   = useState(false)
  const [resetting, setResetting] = useState(false)

  async function load() {
    setLoading(true)
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
      setLocked(w.manual_overrides ?? {})
    } catch {}
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  function handleChange(key: string, val: number) {
    setDraft(d => ({ ...d, [key]: val }))
    setLocked(l => ({ ...l, [key]: true }))
  }

  function handleUnlock(key: string) {
    setLocked(l => { const n = { ...l }; delete n[key]; return n })
    if (weights) setDraft(d => ({ ...d, [key]: (weights as any)[key] }))
  }

  async function save() {
    setSaving(true)
    try {
      // Send only the locked (manually chosen) values
      const payload: Record<string, number> = {}
      for (const key of Object.keys(locked)) {
        if (locked[key] && draft[key] !== undefined) payload[key] = draft[key]
      }
      // If a field was unlocked (removed from locked), we still need to tell the
      // bot. For simplicity, send all draft values; the server updates manual_overrides
      // based on what was in the request body (non-null fields get locked).
      // For unlocked fields, we send null to remove the override.
      const full: Record<string, number | null> = {
        min_score:           locked['min_score']           ? draft['min_score']           : null,
        atr_stop_multiple:   locked['atr_stop_multiple']   ? draft['atr_stop_multiple']   : null,
        atr_target_multiple: locked['atr_target_multiple'] ? draft['atr_target_multiple'] : null,
        time_window_minutes: locked['time_window_minutes'] ? draft['time_window_minutes'] : null,
      }
      const r = await fetch('/api/optimize/patch', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(full),
      })
      if (!r.ok) throw new Error()
      toast.success('Strategy weights saved')
      load()
    } catch {
      toast.error('Could not save — bot offline?')
    } finally {
      setSaving(false)
    }
  }

  async function resetAll() {
    if (!confirm('איפוס לברירות מחדל? כל הנעילות הידניות יוסרו והself-tuner ינהל הכל מחדש.')) return
    setResetting(true)
    try {
      const r = await fetch('/api/optimize/reset', { method: 'POST' })
      if (!r.ok) throw new Error()
      toast.success('Weights reset to defaults')
      load()
    } catch {
      toast.error('Could not reset — bot offline?')
    } finally {
      setResetting(false)
    }
  }

  if (loading) {
    return (
      <div className="rounded-xl border border-bg-border bg-bg-card p-5 flex items-center gap-2 text-sm text-muted">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading strategy weights…
      </div>
    )
  }

  if (!weights) {
    return (
      <div className="rounded-xl border border-bg-border bg-bg-card p-5 text-sm text-muted">
        Bot offline — cannot load strategy weights.
      </div>
    )
  }

  const lockedCount = Object.values(locked).filter(Boolean).length

  return (
    <div className="rounded-xl border border-bg-border bg-bg-card overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-bg-border flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Sliders className="h-4 w-4 text-brand-cyan" />
            <h3 className="text-sm font-semibold text-primary">Strategy Weights</h3>
          </div>
          <p className="text-xs text-muted mt-1">
            כוון את הפרמטרים שהבוט משתמש בהם לבחירת עסקאות.
            שדות לא נעולים מנוהלים אוטומטית על-ידי הself-tuner.
          </p>
        </div>

        {/* Self-tuner stats */}
        {weights.win_rate_30d !== null && (
          <div className="text-right shrink-0 space-y-0.5">
            <p className="text-xs text-muted">Self-tuner</p>
            <p className="text-xs font-mono text-primary">
              Win {weights.win_rate_30d}% · {weights.update_count} updates
            </p>
            <p className={cn(
              'text-[10px] font-medium',
              weights.bias === 'long'  ? 'text-bull' :
              weights.bias === 'short' ? 'text-bear' : 'text-muted',
            )}>
              bias: {weights.bias}
            </p>
          </div>
        )}
      </div>

      {/* Fields */}
      <div className="p-5 space-y-3">
        {Object.keys(FIELD_META).map(key => (
          <FieldRow
            key={key}
            fieldKey={key}
            value={draft[key] ?? (weights as any)[key]}
            defaultValue={(weights.defaults as any)[key]}
            isLocked={!!locked[key]}
            onChange={v => handleChange(key, v)}
            onUnlock={() => handleUnlock(key)}
          />
        ))}
      </div>

      {/* Footer */}
      <div className="px-5 pb-5 flex items-center justify-between gap-3">
        <button
          onClick={resetAll}
          disabled={resetting}
          className="flex items-center gap-1.5 text-xs text-bear hover:text-bear/80 transition-colors disabled:opacity-50"
        >
          {resetting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
          איפוס לברירות מחדל
        </button>

        <div className="flex items-center gap-3">
          {lockedCount > 0 && (
            <span className="text-[10px] text-muted">
              {lockedCount} שדה{lockedCount > 1 ? 'ות' : ''} נעול{lockedCount > 1 ? 'ות' : ''} ידנית
            </span>
          )}
          <button
            onClick={save}
            disabled={saving}
            className="flex items-center gap-1.5 btn-primary text-xs disabled:opacity-50"
          >
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            שמור
          </button>
        </div>
      </div>
    </div>
  )
}
