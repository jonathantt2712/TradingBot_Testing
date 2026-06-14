import crypto from 'crypto'

// Avoid visually ambiguous characters (0/O, 1/l/I) for readability when typed by hand.
const CHARSET = 'ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789'

export function generateTemporaryPassword(length = 12): string {
  let password = ''
  for (const byte of crypto.randomBytes(length)) {
    password += CHARSET[byte % CHARSET.length]
  }
  return password
}
