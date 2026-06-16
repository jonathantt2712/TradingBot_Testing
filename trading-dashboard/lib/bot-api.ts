/**
 * Thin client for the Python bot FastAPI server.
 * Locally that's localhost:8000; on Vercel set TRADING_BOT_API_URL to the
 * tunnel URL (e.g. https://your-name.ngrok-free.app) pointing at your PC.
 * Every request has a timeout so pages fail fast when the bot is offline.
 */

// trim() guards against stray whitespace/CR sneaking into the env var
// (e.g. `echo url | vercel env add` on Windows appends \r\n).
const BOT_URL = (process.env.TRADING_BOT_API_URL ?? process.env.BOT_URL ?? 'http://localhost:8000')
  .trim()
  .replace(/\/+$/, '')
// Tunnelled requests (Vercel → ngrok → your PC) need more headroom than localhost.
const BOT_TIMEOUT = 8_000  // ms

// ngrok's free tier serves an interstitial warning page unless this header is
// present; harmless when talking to localhost or any other backend.
const BOT_SECRET = process.env.BOT_API_SECRET ?? ''
const BOT_HEADERS: Record<string, string> = {
  'ngrok-skip-browser-warning': '1',
  ...(BOT_SECRET ? { 'x-bot-secret': BOT_SECRET } : {}),
}

function abortAfter(ms: number): AbortSignal {
  const ctrl = new AbortController()
  setTimeout(() => ctrl.abort(), ms)
  return ctrl.signal
}

export async function botGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BOT_URL}${path}`, {
    headers:   BOT_HEADERS,
    signal:    abortAfter(BOT_TIMEOUT),
    cache:     'no-store',
  })
  if (!res.ok) throw new Error(`Bot API ${path} -> ${res.status}`)
  return res.json()
}

export async function botPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BOT_URL}${path}`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', ...BOT_HEADERS },
    body:    JSON.stringify(body),
    signal:  abortAfter(BOT_TIMEOUT),
    cache:   'no-store',
  })
  if (!res.ok) throw new Error(`Bot API POST ${path} -> ${res.status}`)
  return res.json()
}
