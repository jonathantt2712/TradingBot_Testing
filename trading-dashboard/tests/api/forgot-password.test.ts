import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/lib/prisma', () => ({
  prisma: {
    user: {
      findFirst: vi.fn(),
      update: vi.fn(),
    },
  },
}))
vi.mock('@/lib/brevo', () => ({
  sendTemporaryPasswordEmail: vi.fn(),
}))

import { prisma } from '@/lib/prisma'
import { sendTemporaryPasswordEmail } from '@/lib/brevo'
import { POST } from '@/app/api/auth/forgot-password/route'

function makeRequest(body: unknown) {
  return new Request('http://localhost/api/auth/forgot-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

describe('POST /api/auth/forgot-password', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns success without sending an email when the user does not exist', async () => {
    vi.mocked(prisma.user.findFirst).mockResolvedValue(null)

    const res = await POST(makeRequest({ email: 'nobody@example.com' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true })
    expect(prisma.user.update).not.toHaveBeenCalled()
    expect(sendTemporaryPasswordEmail).not.toHaveBeenCalled()
  })

  it('sets a temporary password and emails it when the user exists', async () => {
    vi.mocked(prisma.user.findFirst).mockResolvedValue({ id: 'user-1', email: 'real@example.com' } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({} as any)
    vi.mocked(sendTemporaryPasswordEmail).mockResolvedValue(undefined)

    const res = await POST(makeRequest({ email: 'real@example.com' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true })

    const updateArgs = vi.mocked(prisma.user.update).mock.calls[0][0] as any
    expect(updateArgs.where).toEqual({ id: 'user-1' })
    expect(typeof updateArgs.data.passwordHash).toBe('string')
    expect(updateArgs.data.mustChangePassword).toBe(true)

    expect(sendTemporaryPasswordEmail).toHaveBeenCalledWith('real@example.com', expect.any(String))
  })

  it('returns success even if sending the email fails', async () => {
    vi.mocked(prisma.user.findFirst).mockResolvedValue({ id: 'user-1', email: 'real@example.com' } as any)
    vi.mocked(prisma.user.update).mockResolvedValue({} as any)
    vi.mocked(sendTemporaryPasswordEmail).mockRejectedValue(new Error('Brevo down'))

    const res = await POST(makeRequest({ email: 'real@example.com' }))
    const data = await res.json()

    expect(res.status).toBe(200)
    expect(data).toEqual({ success: true })
  })
})
