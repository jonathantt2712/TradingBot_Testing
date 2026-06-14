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
import { POST } from '@/app/api/auth/reset-password/route'

function makeRequest(body: unknown) {
  return new Request('http://localhost/api/auth/reset-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

describe('POST /api/auth/reset-password', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('rejects unauthenticated requests', async () => {
    vi.mocked(auth).mockResolvedValue(null as any)

    const res = await POST(makeRequest({ password: 'newpass123' }))
    const data = await res.json()

    expect(res.status).toBe(401)
    expect(data.error).toBeTruthy()
    expect(prisma.user.update).not.toHaveBeenCalled()
  })

  it('rejects requests missing a password', async () => {
    vi.mocked(auth).mockResolvedValue({ user: { id: 'user-1' } } as any)

    const res = await POST(makeRequest({}))
    const data = await res.json()

    expect(res.status).toBe(400)
    expect(data.error).toBeTruthy()
    expect(prisma.user.update).not.toHaveBeenCalled()
  })

  it('updates the password and clears mustChangePassword', async () => {
    vi.mocked(auth).mockResolvedValue({ user: { id: 'user-1' } } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({} as any)

    const res = await POST(makeRequest({ password: 'newpass123' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true })

    const updateArgs = vi.mocked(prisma.user.update).mock.calls[0][0] as any
    expect(updateArgs.where).toEqual({ id: 'user-1' })
    expect(updateArgs.data.mustChangePassword).toBe(false)
    expect(typeof updateArgs.data.passwordHash).toBe('string')
    expect(updateArgs.data.passwordHash).not.toBe('newpass123')
  })
})
