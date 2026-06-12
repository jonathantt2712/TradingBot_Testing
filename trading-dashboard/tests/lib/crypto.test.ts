import { describe, it, expect } from 'vitest'
import { encrypt, decrypt } from '@/lib/crypto'

describe('lib/crypto', () => {
  it('round-trips a string through encrypt/decrypt', () => {
    const plaintext = 'PKBDDZ2MMKE6P2JREVXEVTBLZ3'
    const ciphertext = encrypt(plaintext)
    expect(ciphertext).not.toBe(plaintext)
    expect(decrypt(ciphertext)).toBe(plaintext)
  })

  it('produces different ciphertext for the same input each time', () => {
    const plaintext = 'same-secret'
    expect(encrypt(plaintext)).not.toBe(encrypt(plaintext))
  })

  it('throws if ENCRYPTION_KEY is missing', () => {
    const original = process.env.ENCRYPTION_KEY
    delete process.env.ENCRYPTION_KEY
    expect(() => encrypt('x')).toThrow()
    process.env.ENCRYPTION_KEY = original
  })
})
