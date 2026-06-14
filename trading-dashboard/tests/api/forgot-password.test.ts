import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/lib/prisma', () => ({
  prisma: {
    user: {
      findUnique: vi.fn(),
      update: vi.fn(),
    },
  },
}))
vi.mock('@/lib/brevo', () => ({
  sendPasswordResetEmail: vi.fn(),
}))

import { prisma } from '@/lib/prisma'
import { sendPasswordResetEmail } from '@/lib/brevo'
import { POST } from '@/app/api/auth/forgot-password/route'

function makeRequest(body: unknown) {
  return new Request('http://localhost/api/auth/forgot-password', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      host: 'localhost:3000',
      'x-forwarded-proto': 'http',
    },
    body: JSON.stringify(body),
  })
}

describe('POST /api/auth/forgot-password', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns success without sending an email when the user does not exist', async () => {
    vi.mocked(prisma.user.findUnique).mockResolvedValue(null)

    const res = await POST(makeRequest({ email: 'nobody@example.com' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true })
    expect(prisma.user.update).not.toHaveBeenCalled()
    expect(sendPasswordResetEmail).not.toHaveBeenCalled()
  })

  it('stores a reset token and sends an email when the user exists', async () => {
    vi.mocked(prisma.user.findUnique).mockResolvedValue({ id: 'user-1', email: 'real@example.com' } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({} as any)
    vi.mocked(sendPasswordResetEmail).mockResolvedValue(undefined)

    const res = await POST(makeRequest({ email: 'real@example.com' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true })
    expect(prisma.user.update).toHaveBeenCalledWith({
      where: { id: 'user-1' },
      data: expect.objectContaining({
        resetTokenHash: expect.any(String),
        resetTokenExpiry: expect.any(Date),
      }),
    })
    expect(sendPasswordResetEmail).toHaveBeenCalledWith(
      'real@example.com',
      expect.stringContaining('http://localhost:3000/reset-password?token=')
    )
  })

  it('returns success even if sending the email fails', async () => {
    vi.mocked(prisma.user.findUnique).mockResolvedValue({ id: 'user-1', email: 'real@example.com' } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({} as any)
    vi.mocked(sendPasswordResetEmail).mockRejectedValue(new Error('Brevo down'))

    const res = await POST(makeRequest({ email: 'real@example.com' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true })
  })
})
