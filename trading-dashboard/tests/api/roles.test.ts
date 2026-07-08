import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/auth', () => ({
  auth: vi.fn(),
}))
vi.mock('@/lib/prisma', () => ({
  prisma: { user: { update: vi.fn(), findUnique: vi.fn() } },
}))
vi.mock('@/lib/bot-api', () => ({
  botGet:  vi.fn().mockResolvedValue({}),
  botPost: vi.fn().mockResolvedValue({ status: 'ok' }),
}))

import { auth } from '@/auth'
import { botPost } from '@/lib/bot-api'
import { prisma } from '@/lib/prisma'
import { POST as tradeModePost } from '@/app/api/trade-mode/route'
import { POST as brokerModePost } from '@/app/api/broker-mode/route'
import { POST as optimizeReset } from '@/app/api/optimize/reset/route'

function req(body: unknown) {
  return new Request('http://localhost/api/x', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

const OWNER  = { user: { id: 'u1', role: 'owner' } }
const VIEWER = { user: { id: 'u2', role: 'viewer' } }

describe('shared-bot controls are owner-only', () => {
  beforeEach(() => vi.clearAllMocks())

  it('trade-mode is PER-USER — any signed-in user saves their own preference, bot untouched', async () => {
    // Design from main: this toggle controls whether the DASHBOARD executes
    // on this user's behalf; the bot's server-side loop is a separate
    // mechanism (AUTO_EXECUTE_ON_RAILWAY). Not shared state → not owner-gated.
    vi.mocked(auth).mockResolvedValue(VIEWER as any)
    vi.mocked(prisma.user.update).mockResolvedValue({} as any)
    const res = await tradeModePost(req({ auto_execute: true }))
    expect(res.status).toBe(200)
    expect(prisma.user.update).toHaveBeenCalledWith(
      expect.objectContaining({ where: { id: 'u2' } }),
    )
    expect(botPost).not.toHaveBeenCalled()
  })

  it('owner can switch broker', async () => {
    vi.mocked(auth).mockResolvedValue(OWNER as any)
    const res = await brokerModePost(req({ broker: 'ibkr' }))
    expect(res.status).toBe(200)
    expect(botPost).toHaveBeenCalledWith('/api/broker-mode', { broker: 'ibkr' })
  })

  it('viewer cannot switch broker (which flattens positions)', async () => {
    vi.mocked(auth).mockResolvedValue(VIEWER as any)
    const res = await brokerModePost(req({ broker: 'ibkr' }))
    expect(res.status).toBe(403)
    expect(botPost).not.toHaveBeenCalled()
  })

  it('viewer cannot reset strategy weights', async () => {
    vi.mocked(auth).mockResolvedValue(VIEWER as any)
    const res = await optimizeReset()
    expect(res.status).toBe(403)
    expect(botPost).not.toHaveBeenCalled()
  })

  it('unauthenticated is still 401, not 403', async () => {
    vi.mocked(auth).mockResolvedValue(null as any)
    const res = await tradeModePost(req({ auto_execute: true }))
    expect(res.status).toBe(401)
  })
})
