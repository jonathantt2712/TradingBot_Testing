'use client'
import { useMemo } from 'react'
import {
  ComposedChart, Bar, Cell, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
} from 'recharts'
import type { OhlcBar } from '@/types/trading'

export interface TradeMarker {
  t:     string                 // ISO timestamp
  price: number
  kind:  'entry' | 'exit'
}

const UP = '#22C55E'
const DOWN = '#EF4444'

// Triangle marker: ▲ green for entries (below the bar), ▼ red for exits (above).
function Triangle({ cx, cy, fill, up }: any) {
  if (cx == null || cy == null) return null
  const s = 6
  const pts = up
    ? `${cx},${cy - s} ${cx - s},${cy + s} ${cx + s},${cy + s}`
    : `${cx},${cy + s} ${cx - s},${cy - s} ${cx + s},${cy - s}`
  return <polygon points={pts} fill={fill} stroke="#0F172A" strokeWidth={0.5} />
}

export default function CandleChart({ bars, markers }: { bars: OhlcBar[]; markers: TradeMarker[] }) {
  const rows = useMemo(() => {
    const base = bars.map((b, i) => ({
      i,
      t: b.t,
      wick: [b.l, b.h] as [number, number],
      body: [Math.min(b.o, b.c), Math.max(b.o, b.c)] as [number, number],
      up: b.c >= b.o,
      entry: undefined as number | undefined,
      exit: undefined as number | undefined,
    }))
    // Snap each marker to the first bar at/after its timestamp.
    const ts = bars.map(b => +new Date(b.t))
    for (const m of markers) {
      const mt = +new Date(m.t)
      let idx = ts.findIndex(x => x >= mt)
      if (idx < 0) idx = base.length - 1
      if (idx >= 0 && base[idx]) base[idx][m.kind] = m.price
    }
    return base
  }, [bars, markers])

  const [lo, hi] = useMemo(() => {
    if (!bars.length) return [0, 1]
    const lows = bars.map(b => b.l), highs = bars.map(b => b.h)
    const min = Math.min(...lows), max = Math.max(...highs)
    const pad = (max - min) * 0.05 || 1
    return [+(min - pad).toFixed(2), +(max + pad).toFixed(2)]
  }, [bars])

  if (!bars.length) {
    return <div className="h-72 flex items-center justify-center text-sm text-muted">No bars for this ticker.</div>
  }

  return (
    <div className="h-72">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
          <XAxis dataKey="i" tick={{ fontSize: 10, fill: '#64748B' }} />
          <YAxis domain={[lo, hi]} tick={{ fontSize: 10, fill: '#64748B' }} width={48} />
          <Tooltip
            contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', fontSize: 12 }}
            formatter={(v: any, n: any) => [Array.isArray(v) ? `${v[0]} – ${v[1]}` : v, n]}
          />
          {/* wick (thin) then body (wide), coloured per direction */}
          <Bar dataKey="wick" barSize={1} isAnimationActive={false}>
            {rows.map((r, i) => <Cell key={i} fill={r.up ? UP : DOWN} />)}
          </Bar>
          <Bar dataKey="body" barSize={6} isAnimationActive={false}>
            {rows.map((r, i) => <Cell key={i} fill={r.up ? UP : DOWN} />)}
          </Bar>
          <Scatter dataKey="entry" isAnimationActive={false}
                   shape={(p: any) => <Triangle {...p} fill={UP} up />} />
          <Scatter dataKey="exit" isAnimationActive={false}
                   shape={(p: any) => <Triangle {...p} fill={DOWN} up={false} />} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
