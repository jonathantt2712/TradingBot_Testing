/**
 * Thin client for the Python bot FastAPI server at localhost:8000.
 * Every request has a 3-second AbortSignal timeout so pages fail fast
 * when the bot server is offline — no hanging requests.
 */

const BOT_URL     = process.env.TRADING_BOT_API_URL ?? process.env.BOT_URL ?? 'http://localhost:8000'
const BOT_TIMEOUT = 3_000  // ms

function abortAfter(ms: number): AbortSignal {
  const ctrl = new AbortController()
  setTimeout(() => ctrl.abort(), ms)
  return ctrl.signal
}

export async function botGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BOT_URL}${path}`, {
    signal:    abortAfter(BOT_TIMEOUT),
    cache:     'no-store',
    next:      { revalidate: 0 },
  })
  if (!res.ok) throw new Error(`Bot API ${path} -> ${res.status}`)
  return res.json()
}

export async function botPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BOT_URL}${path}`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
    signal:  abortAfter(BOT_TIMEOUT),
    cache:   'no-store',
  })
  if (!res.ok) throw new Error(`Bot API POST ${path} -> ${res.status}`)
  return res.json()
}
