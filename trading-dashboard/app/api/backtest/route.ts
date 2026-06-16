import { NextResponse } from 'next/server'
import { readFileSync }  from 'fs'
import { resolve }       from 'path'
import { auth }          from '@/auth'

function readJson(relPath: string): any {
  try {
    const abs = resolve(process.cwd(), '..', relPath)
    return JSON.parse(readFileSync(abs, 'utf-8'))
  } catch {
    return null
  }
}

export async function GET() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  const results = readJson('backtest_results.json')
  const optimal = readJson('backtest_optimal.json')
  const config  = readJson('OPTIMAL_CONFIG.txt')   // text, not JSON

  // Read OPTIMAL_CONFIG.txt as text
  let configText: string | null = null
  try {
    const abs = resolve(process.cwd(), '..', 'OPTIMAL_CONFIG.txt')
    configText = readFileSync(abs, 'utf-8')
  } catch { /* ignore */ }

  return NextResponse.json({
    results: results ?? null,
    optimal: optimal ?? null,
    configText: configText ?? null,
  }, {
    headers: { 'Cache-Control': 'no-store' },
  })
}
