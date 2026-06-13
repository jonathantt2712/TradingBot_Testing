import { describe, it, expect } from 'vitest'
import { generateResetToken, hashResetToken, RESET_TOKEN_TTL_MS } from '@/lib/resetToken'

describe('lib/resetToken', () => {
  it('generates a token whose hash matches hashResetToken', () => {
    const { token, hash } = generateResetToken()
    expect(hashResetToken(token)).toBe(hash)
  })

  it('generates different tokens each time', () => {
    const a = generateResetToken()
    const b = generateResetToken()
    expect(a.token).not.toBe(b.token)
    expect(a.hash).not.toBe(b.hash)
  })

  it('sets an expiry roughly 1 hour in the future', () => {
    const { expiresAt } = generateResetToken()
    const diff = expiresAt.getTime() - Date.now()
    expect(diff).toBeGreaterThan(RESET_TOKEN_TTL_MS - 5000)
    expect(diff).toBeLessThanOrEqual(RESET_TOKEN_TTL_MS)
  })
})
