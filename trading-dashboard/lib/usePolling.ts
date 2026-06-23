'use client'
import { useEffect, useRef } from 'react'

/**
 * Run `fn` once on mount, then every `ms` — but skip ticks while the browser
 * tab is hidden, and fire an immediate refresh when it becomes visible again.
 * Always clears the interval on unmount.
 *
 * Replaces the per-page `useEffect(() => { fn(); setInterval(fn, ms) ... })`
 * pattern so a backgrounded dashboard stops hammering the API (saves Railway
 * load + battery) and re-syncs the moment you return to it.
 */
export function usePolling(fn: () => void, ms: number): void {
  const saved = useRef(fn)
  saved.current = fn // always call the latest fn without restarting the interval

  useEffect(() => {
    const run = () => { if (!document.hidden) saved.current() }
    run() // initial load
    const id = setInterval(run, ms)
    document.addEventListener('visibilitychange', run)
    return () => {
      clearInterval(id)
      document.removeEventListener('visibilitychange', run)
    }
  }, [ms])
}
