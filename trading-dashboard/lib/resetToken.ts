import crypto from 'crypto'

export const RESET_TOKEN_TTL_MS = 60 * 60 * 1000 // 1 hour

export function hashResetToken(token: string): string {
  return crypto.createHash('sha256').update(token).digest('hex')
}

export function generateResetToken(): { token: string; hash: string; expiresAt: Date } {
  const token = crypto.randomBytes(32).toString('hex')
  return {
    token,
    hash: hashResetToken(token),
    expiresAt: new Date(Date.now() + RESET_TOKEN_TTL_MS),
  }
}
