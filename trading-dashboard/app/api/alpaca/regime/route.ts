import { NextResponse }   from 'next/server'
import { getAlpacaCreds } from '@/lib/session'
import { computeRegime }  from '@/lib/regime'

export const dynamic = 'force-dynamic'

export async function GET() {
  const creds = await getAlpacaCreds()
  if (!creds) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    return NextResponse.json(await computeRegime(creds))
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 502 })
  }
}
