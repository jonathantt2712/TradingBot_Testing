'use client'
import { useEffect, useRef } from 'react'

/**
 * Poll `fn` every `ms` milliseconds, with tab-visibility awareness.
 *
 * @param fn  - Function to call on each tick
 * @param ms  - Interval in milliseconds
 * @param skipInitialRun - Set true when the component already has fresh SSR
 *   data as initial props. Without this, the first tick fires immediately on
 *   mount and overwrites the SSR values with potentially stale API data,
 *   causing a visible flicker. Example: LiveDashboard receives initialStats
 *   from the server — no need to re-fetch on mount.
 *
 * RULE: if the component receives `initial*` props from a server component,
 * always pass `skipInitialRun: true`. If the component starts with empty
 * state and fills via polling, leave it as the default (false).
 */
export function usePolling(
  fn: () => void,
  ms: number,
  { skipInitialRun = false }: { skipInitialRun?: boolean } = {},
): void {
  const saved = useRef(fn)
  saved.current = fn

  useEffect(() => {
    const run = () => { if (!document.hidden) saved.current() }
    if (!skipInitialRun) run()
    const id = setInterval(run, ms)
    document.addEventListener('visibilitychange', run)
    return () => {
      clearInterval(id)
      document.removeEventListener('visibilitychange', run)
    }
  }, [ms, skipInitialRun])
}
