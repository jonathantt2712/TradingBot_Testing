export interface SignalDetail {
  name:        string
  display:     string
  raw:         string
  score?:      number
  direction?:  string
  note:        string
  weight_pct?: number
}

export type ReasoningSection =
  | { type: 'warning'; text: string }
  | { type: 'signals'; signals: SignalDetail[] }
  | { type: 'list';    label: string; items: string[] }
  | { type: 'text';    label: string; text: string }
  | { type: 'grid';    label: string; entries: [string, string][] }

const LIST_KEYS: { key: string; label: string }[] = [
  { key: 'headlines_sample',      label: 'Recent headlines' },
  { key: 'bull_phrases_matched',  label: 'Bullish phrases matched' },
  { key: 'bear_phrases_matched',  label: 'Bearish phrases matched' },
  { key: 'bull_keywords_matched', label: 'Bullish keywords matched' },
  { key: 'bear_keywords_matched', label: 'Bearish keywords matched' },
]

const TEXT_KEYS: { key: string; label: string }[] = [
  { key: 'llm_rationale', label: 'LLM analysis' },
  { key: 'analysis',      label: 'Chart analysis' },
]

const GRID_KEYS: { key: string; label: string }[] = [
  { key: 'plan',             label: 'Plan' },
  { key: 'sizing',           label: 'Sizing' },
  { key: 'inputs',           label: 'Inputs' },
  { key: 'thresholds',       label: 'Thresholds' },
  { key: 'threshold_shifts', label: 'Threshold shifts' },
  { key: 'rules',            label: 'Regime rules' },
]

const HANDLED_KEYS = new Set<string>([
  'signals', 'veto', 'veto_reason', 'note', 'regime', 'rationale', 'pattern_identified',
  ...GRID_KEYS.map(g => g.key),
  ...LIST_KEYS.map(l => l.key),
  ...TEXT_KEYS.map(t => t.key),
])

/**
 * Normalizes the varying per-agent `reasoning` dict shapes into an ordered
 * list of renderable sections. See docs/superpowers/specs/2026-06-15-agents-page-design.md
 * for the documented shape of each agent role's reasoning dict.
 */
export function reasoningToSections(reasoning: Record<string, any> | null | undefined): ReasoningSection[] {
  if (!reasoning || typeof reasoning !== 'object') return []

  const sections: ReasoningSection[] = []

  if (reasoning.veto === true && typeof reasoning.veto_reason === 'string') {
    sections.push({ type: 'warning', text: reasoning.veto_reason })
  }

  if (Array.isArray(reasoning.signals)) {
    sections.push({ type: 'signals', signals: reasoning.signals as SignalDetail[] })
  }

  for (const { key, label } of LIST_KEYS) {
    const items = reasoning[key]
    if (Array.isArray(items) && items.length > 0) {
      sections.push({ type: 'list', label, items })
    }
  }

  for (const { key, label } of TEXT_KEYS) {
    const text = reasoning[key]
    if (typeof text === 'string' && text.length > 0) {
      const label_ = key === 'analysis' && typeof reasoning.pattern_identified === 'string' && reasoning.pattern_identified
        ? `Pattern: ${reasoning.pattern_identified}`
        : label
      sections.push({ type: 'text', label: label_, text })
    }
  }

  for (const { key, label } of GRID_KEYS) {
    const obj = reasoning[key]
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
      const entries = Object.entries(obj).map(([k, v]) => [k, String(v)] as [string, string])
      if (entries.length > 0) sections.push({ type: 'grid', label, entries })
    }
  }

  const detailEntries: [string, string][] = []
  for (const [key, value] of Object.entries(reasoning)) {
    if (HANDLED_KEYS.has(key)) continue
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      detailEntries.push([key, String(value)])
    }
  }
  if (detailEntries.length > 0) {
    sections.push({ type: 'grid', label: 'Details', entries: detailEntries })
  }

  if (typeof reasoning.note === 'string' && reasoning.note.length > 0) {
    sections.push({ type: 'text', label: 'Note', text: reasoning.note })
  }

  return sections
}
