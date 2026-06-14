import { describe, it, expect, vi, beforeEach } from 'vitest'
import bcrypt from 'bcryptjs'

vi.mock('@/auth', () => ({
  auth: vi.fn(),
}))
vi.mock('@/lib/prisma', () => ({
  prisma: {
    user: {
      findUnique: vi.fn(),
      update: vi.fn(),
    },
  },
}))

import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'
import { POST } from '@/app/api/auth/change-password/route'

function makeRequest(body: unknown) {
  return new Request('http://localhost/api/auth/change-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

describe('POST /api/auth/change-password', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('rejects unauthenticated requests', async () => {
    vi.mocked(auth).mockResolvedValue(null as any)

    const res = await POST(makeRequest({ currentPassword: 'old', newPassword: 'new' }))
    const data = await res.json()

    expect(res.status).toBe(401)
    expect(data.error).toBeTruthy()
    expect(prisma.user.findUnique).not.toHaveBeenCalled()
  })

  it('rejects requests missing current or new password', async () => {
    vi.mocked(auth).mockResolvedValue({ user: { id: 'user-1' } } as any)

    const res = await POST(makeRequest({ currentPassword: 'old' }))
    const data = await res.json()

    expect(res.status).toBe(400)
    expect(data.error).toBeTruthy()
    expect(prisma.user.findUnique).not.toHaveBeenCalled()
  })

  it('rejects an incorrect current password', async () => {
    vi.mocked(auth).mockResolvedValue({ user: { id: 'user-1' } } as any)
    const passwordHash = await bcrypt.hash('correct-password', 10)
    vi.mocked(prisma.user.findUnique).mockResolvedValue({ id: 'user-1', passwordHash } as any)

    const res = await POST(makeRequest({ currentPassword: 'wrong-password', newPassword: 'newpass123' }))
    const data = await res.json()

    expect(res.status).toBe(400)
    expect(data.error).toBe('Current password is incorrect')
    expect(prisma.user.update).not.toHaveBeenCalled()
  })

  it('updates the password when the current password is correct', async () => {
    vi.mocked(auth).mockResolvedValue({ user: { id: 'user-1' } } as any)
    const passwordHash = await bcrypt.hash('correct-password', 10)
    vi.mocked(prisma.user.findUnique).mockResolvedValue({ id: 'user-1', passwordHash } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({} as any)

    const res = await POST(makeRequest({ currentPassword: 'correct-password', newPassword: 'newpass123' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true })

    const updateArgs = vi.mocked(prisma.user.update).mock.calls[0][0] as any
    expect(updateArgs.where).toEqual({ id: 'user-1' })
    expect(typeof updateArgs.data.passwordHash).toBe('string')
    expect(updateArgs.data.passwordHash).not.toBe('newpass123')
  })
})
