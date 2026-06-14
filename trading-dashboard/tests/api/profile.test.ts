import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/auth', () => ({
  auth: vi.fn(),
}))
vi.mock('@/lib/prisma', () => ({
  prisma: {
    user: {
      update: vi.fn(),
    },
  },
}))

import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'
import { PATCH } from '@/app/api/auth/profile/route'

function makeRequest(body: unknown) {
  return new Request('http://localhost/api/auth/profile', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

describe('PATCH /api/auth/profile', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('rejects unauthenticated requests', async () => {
    vi.mocked(auth).mockResolvedValue(null as any)

    const res = await PATCH(makeRequest({ phone: '+972501234567' }))
    const data = await res.json()

    expect(res.status).toBe(401)
    expect(data.error).toBeTruthy()
    expect(prisma.user.update).not.toHaveBeenCalled()
  })

  it('rejects an invalid phone number', async () => {
    vi.mocked(auth).mockResolvedValue({ user: { id: 'user-1' } } as any)

    const res = await PATCH(makeRequest({ phone: 'not-a-phone' }))
    const data = await res.json()

    expect(res.status).toBe(400)
    expect(data.error).toBeTruthy()
    expect(prisma.user.update).not.toHaveBeenCalled()
  })

  it('clears the phone number when given an empty string', async () => {
    vi.mocked(auth).mockResolvedValue({ user: { id: 'user-1' } } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({ phone: null } as any)

    const res = await PATCH(makeRequest({ phone: '' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true, phone: null })

    const updateArgs = vi.mocked(prisma.user.update).mock.calls[0][0] as any
    expect(updateArgs.where).toEqual({ id: 'user-1' })
    expect(updateArgs.data).toEqual({ phone: null })
  })

  it('updates the phone number when valid', async () => {
    vi.mocked(auth).mockResolvedValue({ user: { id: 'user-1' } } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({ phone: '+972501234567' } as any)

    const res = await PATCH(makeRequest({ phone: '+972501234567' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true, phone: '+972501234567' })

    const updateArgs = vi.mocked(prisma.user.update).mock.calls[0][0] as any
    expect(updateArgs.where).toEqual({ id: 'user-1' })
    expect(updateArgs.data).toEqual({ phone: '+972501234567' })
  })
})
