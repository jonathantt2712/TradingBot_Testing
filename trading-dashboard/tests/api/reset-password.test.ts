import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/lib/prisma', () => ({
  prisma: {
    user: {
      findFirst: vi.fn(),
      update: vi.fn(),
    },
  },
}))

import { prisma } from '@/lib/prisma'
import { POST } from '@/app/api/auth/reset-password/route'
import { hashResetToken } from '@/lib/resetToken'

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

  it('rejects requests missing token or password', async () => {
    const res = await POST(makeRequest({ token: 'abc' }))
    const data = await res.json()

    expect(res.status).toBe(400)
    expect(data.error).toBeTruthy()
    expect(prisma.user.findFirst).not.toHaveBeenCalled()
  })

  it('rejects an invalid or expired token', async () => {
    vi.mocked(prisma.user.findFirst).mockResolvedValue(null)

    const res = await POST(makeRequest({ token: 'bad-token', password: 'newpass123' }))
    const data = await res.json()

    expect(res.status).toBe(400)
    expect(data.error).toBe('This reset link is invalid or has expired')
    expect(prisma.user.update).not.toHaveBeenCalled()
  })

  it('updates the password and clears the token for a valid token', async () => {
    vi.mocked(prisma.user.findFirst).mockResolvedValue({ id: 'user-1' } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({} as any)

    const res = await POST(makeRequest({ token: 'good-token', password: 'newpass123' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true })

    expect(prisma.user.findFirst).toHaveBeenCalledWith({
      where: {
        resetTokenHash: hashResetToken('good-token'),
        resetTokenExpiry: { gt: expect.any(Date) },
      },
    })

    const updateArgs = vi.mocked(prisma.user.update).mock.calls[0][0] as any
    expect(updateArgs.where).toEqual({ id: 'user-1' })
    expect(updateArgs.data.resetTokenHash).toBeNull()
    expect(updateArgs.data.resetTokenExpiry).toBeNull()
    expect(typeof updateArgs.data.passwordHash).toBe('string')
    expect(updateArgs.data.passwordHash).not.toBe('newpass123')
  })
})
