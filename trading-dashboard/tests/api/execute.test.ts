import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/lib/session', () => ({
  getAlpacaCreds: vi.fn(),
}))
vi.mock('@/lib/alpaca', () => ({
  getAccount:         vi.fn(),
  submitBracketOrder: vi.fn(),
}))
vi.mock('@/lib/bot-api', () => ({
  botPost: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('next/cache', () => ({
  revalidatePath: vi.fn(),
}))

import { getAlpacaCreds } from '@/lib/session'
import { getAccount, submitBracketOrder } from '@/lib/alpaca'
import { POST } from '@/app/api/bot/execute/route'

const CREDS = { keyId: 'k', secret: 's', paper: true }

function makeRequest(body: unknown) {
  return new Request('http://localhost/api/bot/execute', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

const BASE_BODY = {
  recommendation_id: 'r-1',
  ticker:      'AAPL',
  direction:   'LONG' as const,
  qty:         999,        // bot-sized qty — must be ignored
  entry:       100,
  stop_loss:   98,
  take_profit: 106,
}

describe('POST /api/bot/execute', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getAlpacaCreds).mockResolvedValue(CREDS as any)
  })

  it("sizes qty from the executing user's own equity, ignoring body.qty", async () => {
    vi.mocked(getAccount).mockResolvedValue({ equity: '10000' } as any)
    vi.mocked(submitBracketOrder).mockResolvedValue({ id: 'order-1' } as any)

    const res  = await POST(makeRequest({ ...BASE_BODY, recommendation_id: 'r-sized' }))
    const data = await res.json()

    // equity=10000, risk 1% = $100, perShareRisk = 2 -> 50 by risk
    // exposure cap: 20% of 10000 / 100 = 20 -> exposure binds
    expect(data.qty).toBe(20)
    expect(submitBracketOrder).toHaveBeenCalledWith(CREDS, expect.objectContaining({ qty: 20 }))
  })

  it('returns 422 and submits nothing when sized qty is 0', async () => {
    vi.mocked(getAccount).mockResolvedValue({ equity: '10' } as any)

    const res  = await POST(makeRequest({ ...BASE_BODY, recommendation_id: 'r-too-small' }))
    const data = await res.json()

    expect(res.status).toBe(422)
    expect(data.success).toBe(false)
    expect(data.message).toMatch(/too small/i)
    expect(submitBracketOrder).not.toHaveBeenCalled()
  })

  it('returns 401 when the account cannot be fetched', async () => {
    vi.mocked(getAccount).mockRejectedValue(new Error('unauthorized'))

    const res  = await POST(makeRequest({ ...BASE_BODY, recommendation_id: 'r-acct-fail' }))
    const data = await res.json()

    expect(res.status).toBe(401)
    expect(data.success).toBe(false)
    expect(submitBracketOrder).not.toHaveBeenCalled()
  })
})
