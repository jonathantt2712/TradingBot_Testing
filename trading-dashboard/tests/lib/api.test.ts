import { describe, it, expect, vi, afterEach } from 'vitest'

describe('lib/api clientPost error handling', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('throws an Error with the server message and status on failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ success: false, message: 'too small' }),
    }))

    const { api } = await import('@/lib/api')

    await expect(api.execute({
      recommendation_id: 'r1', ticker: 'AAPL', direction: 'LONG', qty: 1,
      entry: 100, stop_loss: 99, take_profit: 102,
    })).rejects.toMatchObject({ message: 'too small', status: 422 })
  })

  it('falls back to a generic message when the body has no message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => null,
    }))

    const { api } = await import('@/lib/api')

    await expect(api.execute({
      recommendation_id: 'r1', ticker: 'AAPL', direction: 'LONG', qty: 1,
      entry: 100, stop_loss: 99, take_profit: 102,
    })).rejects.toMatchObject({ status: 500 })
  })
})

describe('demo data reasoning', () => {
  it('demoRegime includes a reasoning snapshot', async () => {
    const { demoRegime } = await import('@/lib/api')
    const regime = demoRegime()
    expect(regime.reasoning).toBeTruthy()
    expect(regime.reasoning?.inputs).toBeTruthy()
    expect(regime.reasoning?.rules).toBeTruthy()
  })

  it('demoRecommendations evaluations include a reasoning dict for every agent role', async () => {
    const { demoRecommendations } = await import('@/lib/api')
    const recs = demoRecommendations()
    for (const rec of recs) {
      for (const ev of rec.evaluations) {
        expect(ev.reasoning, `${rec.ticker} ${ev.role} reasoning`).toBeTruthy()
      }
    }
  })
})
